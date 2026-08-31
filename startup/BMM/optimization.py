from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
import pickle
import threading
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from ax.api.protocols import IMetric
from blop import (
    Agent,
    default_acquire,
    DOF,
    Objective,
    OutcomeConstraint,
    RangeDOF,
    ScalarizedObjective,
)
from blop.protocols import AcquisitionPlan, Actuator, EvaluationFunction, Sensor
from bluesky.callbacks import CallbackBase
from bluesky.plan_stubs import mv, null, one_nd_step, trigger_and_read
from bluesky.preprocessors import finalize_wrapper, inject_md_wrapper
from bluesky.protocols import Readable
from bluesky.utils import MsgGenerator, plan
from event_model import Event, RunStart, RunStop
import numpy as np
from tiled.client.container import Container

if TYPE_CHECKING:
    import plotly.graph_objects as go

    from blop.ax.agent import _AxAgentMixin


class PromptState(Protocol):
    prompt: bool


@dataclass(frozen=True)
class EnergyAlignmentResources:
    """Live beamline objects used by an energy-alignment run."""

    catalog: Container
    actuators: Mapping[str, Actuator]
    sensors: Mapping[str, Sensor]
    change_edge_plan: Callable[..., MsgGenerator[None]]
    prompt_state: PromptState
    read_energy: Callable[[], float] | None = None


@dataclass(frozen=True)
class BeamEvaluationConfig:
    image_field: str
    intensity_field: str
    crop_region: tuple[int, int]


@dataclass(frozen=True)
class OptimizationConfig:
    iterations: int
    initialization_budget: int
    initialize_with_center: bool


@dataclass(frozen=True)
class EnergyChangeConfig:
    focus: bool = True
    no_hslits: bool = True
    mirror: bool = False


@dataclass(frozen=True)
class EnergyAlignmentProfile:
    """Reusable description of one energy-alignment optimization problem."""

    name: str
    sensors: tuple[str, ...]
    dofs: tuple[DOF, ...]
    objectives: tuple[Objective, ...] | ScalarizedObjective
    outcome_constraints: tuple[OutcomeConstraint, ...]
    evaluation: BeamEvaluationConfig
    optimization: OptimizationConfig
    energy_change: EnergyChangeConfig = EnergyChangeConfig()


@dataclass(frozen=True)
class BeamStats:
    lateral_position: int
    cropped_intensity: float


_IMAGE_EVALUATION_OUTCOMES = frozenset({"lateral_distance", "intensity"})

_ALIGNMENT_DOFS = (
    RangeDOF(
        actuator="dcm_roll",
        bounds=(-0.365 - 10, -0.365 + 10),
        parameter_type="float",
    ),
    RangeDOF(
        actuator="m2_yaw",
        bounds=(-2, 2),
        parameter_type="float",
    ),
    RangeDOF(
        actuator="m2_lateral",
        bounds=(-2, 2),
        parameter_type="float",
    ),
)

_BEAM_EVALUATION = BeamEvaluationConfig(
    image_field="cam-8_image",
    intensity_field="I0",
    crop_region=(900, 1040),
)

PER_ENERGY_ALIGNMENT = EnergyAlignmentProfile(
    name="per-energy-alignment",
    sensors=("camera", "i0"),
    dofs=_ALIGNMENT_DOFS,
    objectives=(Objective(name="lateral_distance", minimize=True),),
    outcome_constraints=(
        OutcomeConstraint(
            "x >= 1000000",
            x=IMetric(name="intensity"),
        ),
    ),
    evaluation=_BEAM_EVALUATION,
    optimization=OptimizationConfig(
        iterations=20,
        initialization_budget=1,
        initialize_with_center=False,
    ),
)

ENERGY_ALIGNMENT_PROFILES: Mapping[str, EnergyAlignmentProfile] = MappingProxyType(
    {PER_ENERGY_ALIGNMENT.name: PER_ENERGY_ALIGNMENT}
)


def get_energy_alignment_profile(
    profile: str | EnergyAlignmentProfile,
) -> EnergyAlignmentProfile:
    """Resolve and validate a named or custom profile."""
    if isinstance(profile, EnergyAlignmentProfile):
        resolved = profile
    else:
        try:
            resolved = ENERGY_ALIGNMENT_PROFILES[profile]
        except KeyError as exc:
            choices = ", ".join(sorted(ENERGY_ALIGNMENT_PROFILES))
            raise ValueError(
                f"Unknown energy-alignment profile {profile!r}; choose from {choices}"
            ) from exc

    _validate_profile(resolved)
    return resolved


def _device_name(device: object) -> str:
    return str(getattr(device, "name", device))


def _objective_names(
    objectives: tuple[Objective, ...] | ScalarizedObjective,
) -> tuple[str, ...]:
    if isinstance(objectives, ScalarizedObjective):
        # Blop currently has no public accessor for the metric names that form a
        # scalarized objective.
        return tuple(objectives._objective_names.values())
    return tuple(objective.name for objective in objectives)


def _constraint_names(
    constraints: tuple[OutcomeConstraint, ...],
) -> tuple[str, ...]:
    # Blop currently exposes only the formatted constraint publicly.
    return tuple(
        outcome.name
        for constraint in constraints
        for outcome in constraint._outcomes.values()
    )


def _optimization_metadata(
    energy: str,
    reference_scan_uid: str | None = None,
    *,
    profile: str | EnergyAlignmentProfile = PER_ENERGY_ALIGNMENT.name,
    resources: EnergyAlignmentResources | None = None,
) -> dict[str, Any]:
    resolved_profile = get_energy_alignment_profile(profile)
    resolved_resources = _resolve_resources(resources)
    _validate_resources(resolved_resources, resolved_profile)
    bound_dofs = _bind_dofs(resolved_resources, resolved_profile)
    beamline_energy = (
        float(resolved_resources.read_energy())
        if resolved_resources.read_energy is not None
        else None
    )

    return {
        "Beamline": {"energy": beamline_energy},
        "BMM_agent": {
            "agent": "blop",
            "plan_name": "search_for_optimal_positions",
            "profile": resolved_profile.name,
            "requested_energy": energy,
            "reference_scan_uid": reference_scan_uid,
            "iterations": resolved_profile.optimization.iterations,
            "dofs": [
                {
                    "name": dof.parameter_name,
                    "actuator": _device_name(dof.actuator),
                    "bounds": list(bounds)
                    if (bounds := getattr(dof, "bounds", None))
                    else None,
                    "parameter_type": getattr(dof, "parameter_type", None),
                }
                for dof in bound_dofs
            ],
            "sensors": [
                _device_name(resolved_resources.sensors[name])
                for name in resolved_profile.sensors
            ],
            "objectives": list(_objective_names(resolved_profile.objectives)),
            "outcome_constraints": [
                str(constraint) for constraint in resolved_profile.outcome_constraints
            ],
        },
    }


def optimization_metadata_wrapper(
    plan,
    energy: str,
    reference_scan_uid: str | None = None,
    *,
    profile: str | EnergyAlignmentProfile = PER_ENERGY_ALIGNMENT.name,
    resources: EnergyAlignmentResources | None = None,
):
    """Inject profile and live beamline metadata into every optimization run."""
    md = _optimization_metadata(
        energy,
        reference_scan_uid,
        profile=profile,
        resources=resources,
    )
    return inject_md_wrapper(plan, md)


def _validate_profile(profile: EnergyAlignmentProfile) -> None:
    if not profile.sensors:
        raise ValueError(f"Profile {profile.name!r} must define at least one sensor")
    if not profile.dofs:
        raise ValueError(f"Profile {profile.name!r} must define at least one DOF")
    if (
        not isinstance(profile.objectives, ScalarizedObjective)
        and not profile.objectives
    ):
        raise ValueError(f"Profile {profile.name!r} must define at least one objective")
    configured_outcomes = set(_objective_names(profile.objectives))
    configured_outcomes.update(_constraint_names(profile.outcome_constraints))
    unsupported_outcomes = configured_outcomes - _IMAGE_EVALUATION_OUTCOMES
    if unsupported_outcomes:
        raise ValueError(
            f"Profile {profile.name!r} uses outcomes not produced by "
            f"ImageEvaluation: {sorted(unsupported_outcomes)!r}"
        )
    if any(dof.actuator is None for dof in profile.dofs):
        raise ValueError(f"Profile {profile.name!r} cannot use unbound, name-only DOFs")

    crop_start, crop_stop = profile.evaluation.crop_region
    if crop_start < 0 or crop_start >= crop_stop:
        raise ValueError(
            f"Invalid image crop region {profile.evaluation.crop_region!r}"
        )

    optimization = profile.optimization
    if optimization.iterations < 1:
        raise ValueError("Optimization iterations must be at least one")
    if not 0 <= optimization.initialization_budget <= optimization.iterations:
        raise ValueError(
            "Initialization budget must be between zero and the iteration count"
        )


def load_bmm_energy_alignment_resources() -> EnergyAlignmentResources:
    """Load live BMM objects lazily so this module remains safe to import."""
    from BMM.edge import change_edge
    from BMM.user_ns.base import bmm_catalog
    from BMM.user_ns.bmm import BMMuser
    from BMM.user_ns.dcm import dcm
    from BMM.user_ns.detectors import cam8, ic0
    from BMM.user_ns.instruments import m2

    if bmm_catalog is None:
        raise RuntimeError("The BMM Tiled catalog is not available in this session")

    def read_energy() -> float:
        try:
            return float(dcm.energy.readback.get())
        except Exception:
            return float(dcm.energy.position)

    return EnergyAlignmentResources(
        catalog=bmm_catalog,
        actuators={
            "dcm_roll": dcm.roll,
            "m2_yaw": m2.yaw,
            "m2_lateral": m2.lateral,
        },
        sensors={"camera": cam8, "i0": ic0},
        change_edge_plan=change_edge,
        prompt_state=BMMuser,
        read_energy=read_energy,
    )


def _resolve_resources(
    resources: EnergyAlignmentResources | None,
) -> EnergyAlignmentResources:
    return resources if resources is not None else load_bmm_energy_alignment_resources()


def _validate_resources(
    resources: EnergyAlignmentResources,
    profile: EnergyAlignmentProfile,
) -> None:
    missing_actuators = [
        dof.actuator
        for dof in profile.dofs
        if isinstance(dof.actuator, str)
        and resources.actuators.get(dof.actuator) is None
    ]
    missing_sensors = [
        sensor for sensor in profile.sensors if resources.sensors.get(sensor) is None
    ]
    if missing_actuators:
        raise ValueError(f"Missing alignment actuators: {missing_actuators!r}")
    if missing_sensors:
        raise ValueError(f"Missing alignment sensors: {missing_sensors!r}")


def _bind_dofs(
    resources: EnergyAlignmentResources,
    profile: EnergyAlignmentProfile,
) -> tuple[DOF, ...]:
    return tuple(
        replace(dof, actuator=resources.actuators[dof.actuator])
        if isinstance(dof.actuator, str)
        else dof
        for dof in profile.dofs
    )


def compute_image_stats(
    image: np.ndarray,
    parameters: BeamEvaluationConfig,
) -> BeamStats:
    """Compute the beam position and intensity from one camera image."""
    gray = np.asarray(image).squeeze()
    if gray.ndim == 3:
        if gray.shape[-1] not in (3, 4):
            raise ValueError(
                "Expected one 2-D or RGB(A) image; "
                f"received ambiguous 3-D shape {gray.shape!r}"
            )
        gray = gray[..., :3].mean(axis=-1)
    if gray.ndim != 2:
        raise ValueError(f"Expected a 2-D image, received shape {gray.shape!r}")
    gray = gray.astype(np.float64, copy=False)

    crop_start, crop_stop = parameters.crop_region
    if crop_stop > gray.shape[1]:
        raise ValueError(
            f"Crop region {parameters.crop_region!r} exceeds image width {gray.shape[1]}"
        )

    cropped_x_profile = gray.sum(axis=0)[crop_start:crop_stop]
    lateral_position = int(np.argmax(cropped_x_profile)) + crop_start
    cropped_intensity = float(cropped_x_profile.sum())
    return BeamStats(
        lateral_position=lateral_position,
        cropped_intensity=cropped_intensity,
    )


def compute_stats(
    uid: str,
    *,
    profile: str | EnergyAlignmentProfile = PER_ENERGY_ALIGNMENT.name,
    resources: EnergyAlignmentResources | None = None,
) -> BeamStats:
    """Print camera and ion-chamber statistics for a completed run."""
    resolved_profile = get_energy_alignment_profile(profile)
    resolved_resources = _resolve_resources(resources)
    run = resolved_resources.catalog[uid]
    evaluation = resolved_profile.evaluation
    image = run["primary"]["data"][evaluation.image_field].read()
    intensity_i0 = run["primary"]["data"][evaluation.intensity_field].read()
    stats = compute_image_stats(image, evaluation)
    print(
        f"lateral_position: {stats.lateral_position}, "
        f"cropped_intensity={stats.cropped_intensity}, "
        f"ic0_intensity={np.asarray(intensity_i0).squeeze()}"
    )
    return stats


class ImageEvaluation:
    """Evaluate camera images against a reference beam position."""

    def __init__(
        self,
        tiled_client: Container,
        reference_scan_uid: str,
        parameters: BeamEvaluationConfig,
    ):
        self.tiled_client = tiled_client
        self.parameters = parameters
        reference_run = self.tiled_client[reference_scan_uid]
        reference_image = reference_run["primary"]["data"][
            parameters.image_field
        ].read()
        reference_stats = compute_image_stats(reference_image, parameters)
        self.target_lateral_position = reference_stats.lateral_position

    def __call__(self, uid: str, suggestions: list[dict]) -> list[dict]:
        if not suggestions:
            return []

        run = self.tiled_client[uid]
        acquired_images = np.asarray(
            run["primary"]["data"][self.parameters.image_field].read()
        )
        if len(suggestions) == 1:
            images = (acquired_images,)
        elif acquired_images.shape[0] == len(suggestions):
            images = tuple(acquired_images[index] for index in range(len(suggestions)))
        else:
            raise ValueError(
                f"Received {len(suggestions)} suggestions but image data has shape "
                f"{acquired_images.shape!r}"
            )

        outcomes = []
        for suggestion, image in zip(suggestions, images, strict=True):
            stats = compute_image_stats(image, self.parameters)
            outcomes.append(
                {
                    "_id": suggestion["_id"],
                    "lateral_distance": abs(
                        self.target_lateral_position - stats.lateral_position
                    ),
                    "intensity": stats.cropped_intensity,
                }
            )
        return outcomes


@plan
def scan_energy(
    detectors: Sequence[Readable],
    *,
    energy_actuator: Actuator,
    energies: Sequence[float],
) -> MsgGenerator[None]:
    """Acquire every configured energy at the current suggested position."""
    devices = [*detectors, energy_actuator]
    for energy in energies:
        yield from mv(energy_actuator, energy)
        yield from trigger_and_read(devices)


def make_energy_scan_acquisition_plan(
    energy_actuator: Actuator,
    energies: Sequence[float],
) -> AcquisitionPlan:
    """Compose Blop's default acquisition with an inner energy scan."""
    energy_points = tuple(energies)
    if not energy_points:
        raise ValueError("Energy scan requires at least one energy")

    return partial(
        default_acquire,
        per_step=partial(
            one_nd_step,
            take_reading=partial(
                scan_energy,
                energy_actuator=energy_actuator,
                energies=energy_points,
            ),
        ),
    )


def make_energy_alignment_agent(
    reference_scan_uid: str,
    *,
    profile: str | EnergyAlignmentProfile = PER_ENERGY_ALIGNMENT.name,
    resources: EnergyAlignmentResources | None = None,
    evaluation_function: EvaluationFunction | None = None,
    acquisition_plan: AcquisitionPlan | None = None,
) -> Agent:
    """Construct a fresh Blop agent from a reusable alignment profile."""
    resolved_profile = get_energy_alignment_profile(profile)
    resolved_resources = _resolve_resources(resources)
    _validate_resources(resolved_resources, resolved_profile)
    if evaluation_function is None:
        evaluation_function = ImageEvaluation(
            resolved_resources.catalog,
            reference_scan_uid=reference_scan_uid,
            parameters=resolved_profile.evaluation,
        )

    agent = Agent(
        sensors=[resolved_resources.sensors[name] for name in resolved_profile.sensors],
        dofs=_bind_dofs(resolved_resources, resolved_profile),
        objectives=resolved_profile.objectives,
        evaluation_function=evaluation_function,
        acquisition_plan=acquisition_plan,
        outcome_constraints=resolved_profile.outcome_constraints,
    )
    agent.ax_client.configure_generation_strategy(
        initialization_budget=resolved_profile.optimization.initialization_budget,
        initialize_with_center=resolved_profile.optimization.initialize_with_center,
    )
    return agent


def _write_energy_map(
    filename: str | Path,
    energy_map: Mapping[str, Any],
) -> None:
    """Atomically persist an energy map using the established pickle format."""
    target = Path(filename)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        with temporary.open("wb") as stream:
            pickle.dump(energy_map, stream, protocol=pickle.HIGHEST_PROTOCOL)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def search_for_optimal_positions(
    energies: list[str],
    reference_scan_uid: str,
    energy_map_filename: str | Path | None = None,
    *,
    profile: str | EnergyAlignmentProfile = PER_ENERGY_ALIGNMENT.name,
    resources: EnergyAlignmentResources | None = None,
) -> MsgGenerator[dict[str, Any]]:
    """Optimize motor positions at each energy using a named or custom profile."""
    resolved_profile = get_energy_alignment_profile(profile)
    resolved_resources = _resolve_resources(resources)
    _validate_resources(resolved_resources, resolved_profile)
    previous_prompt = resolved_resources.prompt_state.prompt

    def main_plan() -> MsgGenerator[dict[str, Any]]:
        energy_map: dict[str, Any] = {}
        evaluation_function: EvaluationFunction | None = None
        resolved_resources.prompt_state.prompt = False

        for energy in energies:
            energy_change = resolved_profile.energy_change
            yield from resolved_resources.change_edge_plan(
                energy,
                focus=energy_change.focus,
                no_hslits=energy_change.no_hslits,
                mirror=energy_change.mirror,
            )

            if evaluation_function is None:
                evaluation_function = ImageEvaluation(
                    resolved_resources.catalog,
                    reference_scan_uid=reference_scan_uid,
                    parameters=resolved_profile.evaluation,
                )
            agent = make_energy_alignment_agent(
                reference_scan_uid,
                profile=resolved_profile,
                resources=resolved_resources,
                evaluation_function=evaluation_function,
            )
            optimize_plan = optimization_metadata_wrapper(
                agent.optimize(resolved_profile.optimization.iterations),
                energy,
                reference_scan_uid,
                profile=resolved_profile,
                resources=resolved_resources,
            )
            yield from optimize_plan

            best_points = agent.get_best_points()
            print(f"best point for {energy} is {best_points}")
            energy_map[energy] = best_points

            if energy_map_filename is not None:
                _write_energy_map(energy_map_filename, energy_map)

        print(f"energy_map={energy_map}")
        return energy_map

    def cleanup_plan() -> MsgGenerator[None]:
        resolved_resources.prompt_state.prompt = previous_prompt
        yield from null()

    return (yield from finalize_wrapper(main_plan(), cleanup_plan()))


# --------- Dash live plotting callback ---------


def _to_list(value: Any) -> list[Any]:
    """Coerce a scalar, NumPy array, or iterable into a plain list."""
    if hasattr(value, "tolist"):
        result = value.tolist()
        return result if isinstance(result, list) else [result]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _py(value: Any) -> Any:
    """Convert a NumPy scalar to its native Python equivalent."""
    return value.item() if hasattr(value, "item") else value


class SurrogateModelDashCallback(CallbackBase):
    """Serve a live 2-D heatmap of an Agent's surrogate-model mean.

    Dash is imported only by :meth:`build_app`, so optimization and figure
    construction remain usable when the optional Dash dependency is absent.
    """

    def __init__(
        self,
        agent: _AxAgentMixin,
        resolution: int = 41,
        update_interval_ms: int = 100,
    ) -> None:
        super().__init__()
        if resolution < 2:
            raise ValueError("Dash plot resolution must be at least two")
        if update_interval_ms < 1:
            raise ValueError("Dash update interval must be positive")

        from ax.core.parameter import ChoiceParameter, ParameterType, RangeParameter

        self._agent = agent
        self._resolution = resolution
        self._update_interval_ms = update_interval_ms
        self._lock = threading.Lock()
        self._version = 0
        self._observed: dict[str, list[Any]] = {}

        experiment = agent.ax_client._experiment
        if experiment is None:
            raise ValueError("The agent's Ax experiment has not been configured")

        self._param_info: dict[str, dict[str, Any]] = {}
        for name, parameter in experiment.parameters.items():
            if isinstance(parameter, RangeParameter):
                self._param_info[name] = {
                    "kind": "range",
                    "lower": float(parameter.lower),
                    "upper": float(parameter.upper),
                    "is_int": parameter.parameter_type == ParameterType.INT,
                }
            elif isinstance(parameter, ChoiceParameter):
                self._param_info[name] = {
                    "kind": "choice",
                    "values": [_py(value) for value in parameter.values],
                }
        self._dof_names = list(self._param_info)
        if not self._dof_names:
            raise ValueError("The agent has no plottable degrees of freedom")

        optimization_config = experiment.optimization_config
        if optimization_config is None:
            raise ValueError(
                "The agent's optimization has not been configured; "
                "no objectives to plot"
            )
        self._objective_names = list(optimization_config.objective.metric_names)
        if not self._objective_names:
            raise ValueError("The agent has no objectives to plot")

    def start(self, doc: RunStart) -> None:
        """Mark the plot stale at the start of an optimization run."""
        with self._lock:
            self._version += 1

    def event(self, doc: Event) -> Event:
        """Record observed DOF values and mark the plot stale."""
        data = doc.get("data", {})
        with self._lock:
            for name in self._dof_names:
                if name in data:
                    self._observed.setdefault(name, []).extend(_to_list(data[name]))
            self._version += 1
        return doc

    def stop(self, doc: RunStop) -> RunStop:
        """Mark the plot stale at the end of an optimization run."""
        with self._lock:
            self._version += 1
        return doc

    @property
    def dof_names(self) -> list[str]:
        """Return the degrees of freedom available for the plot axes."""
        return list(self._dof_names)

    @property
    def objective_names(self) -> list[str]:
        """Return the objectives available for visualization."""
        return list(self._objective_names)

    @property
    def version(self) -> int:
        """Return the version incremented whenever optimization data changes."""
        with self._lock:
            return self._version

    def _axis_values(self, name: str, resolution: int) -> tuple[np.ndarray, bool]:
        info = self._param_info[name]
        if info["kind"] == "range":
            values = np.linspace(info["lower"], info["upper"], resolution)
            if info["is_int"]:
                values = np.unique(np.round(values).astype(int))
            return values, bool(info["is_int"])
        return np.array(info["values"], dtype=object), True

    def _fixed_value(self, name: str) -> Any:
        info = self._param_info[name]
        if info["kind"] == "range":
            midpoint = (info["lower"] + info["upper"]) / 2.0
            return int(round(midpoint)) if info["is_int"] else midpoint
        return info["values"][0]

    def _coerce(self, name: str, value: Any) -> Any:
        info = self._param_info[name]
        if info["kind"] == "range" and info["is_int"]:
            return int(round(float(value)))
        return _py(value)

    def compute_figure(
        self,
        x_name: str,
        y_name: str,
        objective_name: str,
        resolution: int | None = None,
    ) -> go.Figure:
        """Build a heatmap of predicted mean with observed trials overlaid."""
        import plotly.graph_objects as go

        if x_name not in self._param_info or y_name not in self._param_info:
            return self._message_figure("Select available degrees of freedom")
        if objective_name not in self._objective_names:
            return self._message_figure(
                f"Objective {objective_name!r} is not configured"
            )
        if x_name == y_name:
            return self._message_figure(
                "Select two different DOFs for the x and y axes"
            )

        plot_resolution = self._resolution if resolution is None else resolution
        if plot_resolution < 2:
            raise ValueError("Dash plot resolution must be at least two")
        x_values, _ = self._axis_values(x_name, plot_resolution)
        y_values, _ = self._axis_values(y_name, plot_resolution)

        fixed = {name: self._fixed_value(name) for name in self._dof_names}
        points: list[dict[str, Any]] = []
        for y_value in y_values:
            for x_value in x_values:
                point = dict(fixed)
                point[x_name] = self._coerce(x_name, x_value)
                point[y_name] = self._coerce(y_name, y_value)
                points.append(point)

        try:
            predictions = self._agent.ax_client.predict(points)
        except Exception as exc:
            return self._message_figure(
                "Surrogate model is not ready to predict yet.<br>"
                "Run more trials to continue.<br><br>"
                f"({exc})"
            )

        try:
            z = np.array(
                [prediction[objective_name][0] for prediction in predictions],
                dtype=float,
            )
        except KeyError:
            return self._message_figure(
                f"Objective {objective_name!r} is not available in model predictions"
            )
        z = z.reshape(len(y_values), len(x_values))

        figure = go.Figure(
            data=go.Heatmap(
                x=list(x_values),
                y=list(y_values),
                z=z,
                colorscale="Viridis",
                colorbar={"title": objective_name},
                hovertemplate=(
                    f"{x_name}: %{{x}}<br>{y_name}: %{{y}}<br>"
                    f"{objective_name}: %{{z:.4g}}<extra></extra>"
                ),
            )
        )

        with self._lock:
            observed_x = list(self._observed.get(x_name, []))
            observed_y = list(self._observed.get(y_name, []))
        observed_count = min(len(observed_x), len(observed_y))
        if observed_count:
            figure.add_trace(
                go.Scatter(
                    x=observed_x[:observed_count],
                    y=observed_y[:observed_count],
                    mode="markers",
                    marker={
                        "color": "white",
                        "size": 7,
                        "line": {"color": "black", "width": 1},
                    },
                    name="observed",
                    hovertemplate=(
                        f"{x_name}: %{{x}}<br>{y_name}: %{{y}}<extra>observed</extra>"
                    ),
                )
            )

        figure.update_layout(
            title=f"Surrogate mean of {objective_name!r}",
            xaxis_title=x_name,
            yaxis_title=y_name,
            margin={"l": 60, "r": 30, "t": 50, "b": 50},
            template="plotly_white",
        )
        return figure

    @staticmethod
    def _message_figure(message: str) -> go.Figure:
        import plotly.graph_objects as go

        figure = go.Figure()
        figure.add_annotation(
            text=message,
            showarrow=False,
            font={"size": 14},
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
        )
        figure.update_layout(
            xaxis={"visible": False},
            yaxis={"visible": False},
            margin={"l": 20, "r": 20, "t": 20, "b": 20},
            template="plotly_white",
        )
        return figure

    def build_app(self, **dash_kwargs: Any):
        """Build a Dash app; importing Dash is deferred until this call."""
        from dash import Dash, Input, Output, State, dcc, html

        default_x = self._dof_names[0]
        default_y = (
            self._dof_names[1] if len(self._dof_names) > 1 else self._dof_names[0]
        )
        default_objective = self._objective_names[0]

        app = Dash(__name__, **dash_kwargs)
        app.layout = html.Div(
            [
                html.H2("Surrogate model", style={"textAlign": "center"}),
                html.Div(
                    [
                        html.Div(
                            [
                                html.Label("X axis"),
                                dcc.Dropdown(
                                    options=[
                                        {"label": name, "value": name}
                                        for name in self._dof_names
                                    ],
                                    value=default_x,
                                    id="surrogate-x-dropdown",
                                    clearable=False,
                                ),
                            ],
                            style={"flex": "1", "padding": "0 8px"},
                        ),
                        html.Div(
                            [
                                html.Label("Y axis"),
                                dcc.Dropdown(
                                    options=[
                                        {"label": name, "value": name}
                                        for name in self._dof_names
                                    ],
                                    value=default_y,
                                    id="surrogate-y-dropdown",
                                    clearable=False,
                                ),
                            ],
                            style={"flex": "1", "padding": "0 8px"},
                        ),
                        html.Div(
                            [
                                html.Label("Objective"),
                                dcc.Dropdown(
                                    options=[
                                        {"label": name, "value": name}
                                        for name in self._objective_names
                                    ],
                                    value=default_objective,
                                    id="surrogate-objective-dropdown",
                                    clearable=False,
                                ),
                            ],
                            style={"flex": "1", "padding": "0 8px"},
                        ),
                    ],
                    style={
                        "display": "flex",
                        "maxWidth": "900px",
                        "margin": "0 auto",
                    },
                ),
                dcc.Graph(id="surrogate-graph", style={"height": "70vh"}),
                dcc.Interval(
                    id="surrogate-interval",
                    interval=self._update_interval_ms,
                    n_intervals=0,
                ),
                dcc.Store(id="surrogate-rendered-version", data=-1),
            ]
        )

        @app.callback(
            Output("surrogate-graph", "figure"),
            Output("surrogate-rendered-version", "data"),
            Input("surrogate-x-dropdown", "value"),
            Input("surrogate-y-dropdown", "value"),
            Input("surrogate-objective-dropdown", "value"),
            Input("surrogate-interval", "n_intervals"),
            State("surrogate-rendered-version", "data"),
        )
        def _update_graph(
            x_name,
            y_name,
            objective_name,
            _n_intervals,
            rendered_version,
        ):
            from dash import ctx, no_update

            current_version = self.version
            triggered_by_timer = ctx.triggered_id == "surrogate-interval"
            if triggered_by_timer and current_version == rendered_version:
                return no_update, no_update

            figure = self.compute_figure(x_name, y_name, objective_name)
            return figure, current_version

        return app

    def serve(
        self,
        host: str = "127.0.0.1",
        port: int = 8050,
        debug: bool = False,
        **run_kwargs: Any,
    ) -> None:
        """Build the Dash app and run its blocking development server."""
        app = self.build_app()
        app.run(host=host, port=port, debug=debug, **run_kwargs)
