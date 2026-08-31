from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
import pickle
from types import MappingProxyType
from typing import Any, Protocol

from ax.api.protocols import IMetric
from blop import (
    Agent,
    DOF,
    Objective,
    OutcomeConstraint,
    RangeDOF,
    ScalarizedObjective,
)
from blop.protocols import Actuator, Sensor, EvaluationFunction
from bluesky.plan_stubs import null
from bluesky.preprocessors import finalize_wrapper, inject_md_wrapper
from bluesky.utils import MsgGenerator
import numpy as np
from tiled.client.container import Container


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

CAMERA_CENTERING = EnergyAlignmentProfile(
    name="camera-centering",
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

MAXIMUM_INTENSITY = EnergyAlignmentProfile(
    name="maximum-intensity",
    sensors=("camera", "i0"),
    dofs=_ALIGNMENT_DOFS,
    objectives=(Objective(name="intensity", minimize=False),),
    outcome_constraints=(),
    evaluation=_BEAM_EVALUATION,
    optimization=OptimizationConfig(
        iterations=20,
        initialization_budget=1,
        initialize_with_center=False,
    ),
)

ENERGY_ALIGNMENT_PROFILES: Mapping[str, EnergyAlignmentProfile] = MappingProxyType(
    {
        CAMERA_CENTERING.name: CAMERA_CENTERING,
        MAXIMUM_INTENSITY.name: MAXIMUM_INTENSITY,
    }
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

def _device_name(device) -> str:
    return getattr(device, "name", str(device))


def _optimization_metadata(energy: str, reference_scan_uid: str = None) -> dict:
    try:
        beamline_energy = float(dcm.energy.readback.get())
    except Exception:
        beamline_energy = float(dcm.energy.position)

    return {
        "Beamline": {
            "energy": beamline_energy,
        },
        "BMM_agent": {
            "agent": "blop",
            "plan_name": "search_for_optimal_positions",
            "requested_energy": energy,
            "reference_scan_uid": reference_scan_uid,
            "dofs": [
                {
                    "name": _device_name(dof),
                    "actuator": _device_name(dof.actuator),
                    "bounds": list(dof.bounds),
                    "parameter_type": getattr(dof, "parameter_type", None),
                }
                for dof in dofs
            ],
            "sensors": [_device_name(sensor) for sensor in (cam8, ic0)],
            "objectives": [objective.name for objective in objectives],
            "outcome_constraints": [str(constraint) for constraint in outcome_constraints],
        },
    }


def optimization_metadata_wrapper(plan, energy: str, reference_scan_uid: str = None):
    md = _optimization_metadata(energy, reference_scan_uid=reference_scan_uid)
    return inject_md_wrapper(plan, md)

# use this function in bsui for sanity checking
def compute_stats(uid: str):
    image = tiled_client[uid]['primary']['data']['cam8-image'].read()
    intensity_ic0 = tiled_client[uid]['primary']['I0'].read()


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
    gray = np.asarray(image).squeeze().astype(np.float64, copy=False)
    if gray.ndim == 3:
        gray = gray.mean(axis=-1)
    if gray.ndim != 2:
        raise ValueError(f"Expected a 2-D image, received shape {gray.shape!r}")

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
    profile: str | EnergyAlignmentProfile = CAMERA_CENTERING.name,
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


def make_energy_alignment_agent(
    reference_scan_uid: str,
    *,
    profile: str | EnergyAlignmentProfile = CAMERA_CENTERING.name,
    resources: EnergyAlignmentResources | None = None,
) -> Agent:
    """Construct a fresh Blop agent from a reusable alignment profile."""
    resolved_profile = get_energy_alignment_profile(profile)
    resolved_resources = _resolve_resources(resources)
    _validate_resources(resolved_resources, resolved_profile)

    agent = Agent(
        sensors=[resolved_resources.sensors[name] for name in resolved_profile.sensors],
        dofs=_bind_dofs(resolved_resources, resolved_profile),
        objectives=resolved_profile.objectives,
        evaluation_function=ImageEvaluation(
            resolved_resources.catalog,
            reference_scan_uid=reference_scan_uid,
            parameters=resolved_profile.evaluation,
        ),
        outcome_constraints=resolved_profile.outcome_constraints,
    )
    agent.ax_client.configure_generation_strategy(
        initialization_budget=resolved_profile.optimization.initialization_budget,
        initialize_with_center=resolved_profile.optimization.initialize_with_center,
    )
    return agent


def search_for_optimal_positions(
    energies: list[str],
    reference_scan_uid: str,
    energy_map_filename: str | Path | None = None,
    *,
    profile: str | EnergyAlignmentProfile = CAMERA_CENTERING.name,
    resources: EnergyAlignmentResources | None = None,
) -> MsgGenerator[dict[str, Any]]:
    """Optimize motor positions at each energy using a named or custom profile."""
    resolved_profile = get_energy_alignment_profile(profile)
    resolved_resources = _resolve_resources(resources)
    _validate_resources(resolved_resources, resolved_profile)
    previous_prompt = resolved_resources.prompt_state.prompt

<<<<<<< HEAD
    def main_plan() -> MsgGenerator[dict[str, Any]]:
        energy_map: dict[str, Any] = {}
        resolved_resources.prompt_state.prompt = False
=======
        max_iter = 20
        yield from optimization_metadata_wrapper(agent.optimize(max_iter), energy, reference_scan_uid, max_iter)
>>>>>>> 484b29b8fca3b98b90ff71b7fcde88f9de297827

        for energy in energies:
            energy_change = resolved_profile.energy_change
            yield from resolved_resources.change_edge_plan(
                energy,
                focus=energy_change.focus,
                no_hslits=energy_change.no_hslits,
                mirror=energy_change.mirror,
            )

            agent = make_energy_alignment_agent(
                reference_scan_uid,
                profile=resolved_profile,
                resources=resolved_resources,
            )
            yield from agent.optimize(resolved_profile.optimization.iterations)

            best_points = agent.get_best_points()
            print(f"best point for {energy} is {best_points}")
            energy_map[energy] = best_points

<<<<<<< HEAD
            if energy_map_filename is not None:
                with Path(energy_map_filename).open("wb") as stream:
                    pickle.dump(energy_map, stream)

        print(f"energy_map={energy_map}")
        return energy_map

    def cleanup_plan() -> MsgGenerator[None]:
        resolved_resources.prompt_state.prompt = previous_prompt
        yield from null()

    return (yield from finalize_wrapper(main_plan(), cleanup_plan()))
=======
    print(f"{energy_map=}")
    BMMuser.prompt = True

# --------- Dash Live Plotting Callback ---------

"""Live 2D surrogate-model visualization for the Ax :class:`~blop.ax.agent.Agent`.

This module provides :class:`SurrogateModelDashCallback`, a Bluesky callback that
renders the Ax surrogate model's predicted mean as a 2D heatmap and serves it in a
`Plotly Dash <https://dash.plotly.com/>`_ app that updates live as an optimization
runs.

The Dash app exposes three dropdowns:

- **X axis**: any degree of freedom (DOF).
- **Y axis**: any (other) degree of freedom.
- **Objective**: any one of the configured objectives.

The heatmap shows the surrogate model's posterior mean for the selected objective as
a function of the two selected DOFs, with all other DOFs held at the centre of their
range (or their first choice value). Observed trials are overlaid as points.

Notes
-----
Plotly Dash is an optional runtime dependency. It is imported lazily so that simply
importing this module (or the :mod:`blop.callbacks` package) does not require Dash to
be installed. Install it with ``pip install dash``.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

import numpy as np
from bluesky.callbacks import CallbackBase
from event_model import Event, RunStart, RunStop

if TYPE_CHECKING:
    import plotly.graph_objects as go

    from blop.ax.agent import _AxAgentMixin


def _to_list(value: Any) -> list:
    """Coerce a scalar, numpy array, or iterable into a plain list."""
    if hasattr(value, "tolist"):
        result = value.tolist()
        return result if isinstance(result, list) else [result]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _py(value: Any) -> Any:
    """Convert a numpy scalar to its native Python equivalent."""
    return value.item() if hasattr(value, "item") else value


class SurrogateModelDashCallback(CallbackBase):
    """A Bluesky callback that serves a live 2D surrogate-model heatmap via Plotly Dash.

    Subscribe an instance to an :class:`~blop.ax.agent.Agent` (via
    :meth:`Agent.subscribe <blop.ax.agent.Agent.subscribe>`) and call :meth:`serve`
    to start the Dash server. As the agent ingests new trials during an optimization
    run, the callback bumps an internal version counter; the Dash app polls this
    counter on a timer and re-renders the surrogate heatmap whenever new data arrives.

    Parameters
    ----------
    agent : Agent
        The Ax agent whose surrogate model will be visualized. The callback reads the
        agent's DOFs, objectives, and underlying Ax ``Client`` for predictions.
    resolution : int, optional
        The number of grid points per axis used to sample the surrogate model for
        continuous DOFs. Default is 41.
    update_interval_ms : int, optional
        How often (in milliseconds) the Dash app checks for new data. Default is 2000.

    See Also
    --------
    blop.ax.agent.Agent.plot_objective : One-off contour plot via Ax analyses.

    Examples
    --------
    >>> from blop.callbacks.surrogate_dash import SurrogateModelDashCallback
    >>> viz = SurrogateModelDashCallback(agent)
    >>> agent.subscribe(viz)
    >>> # In a separate thread or process, start the server:
    >>> viz.serve(port=8050)  # doctest: +SKIP
    """

    def __init__(
        self,
        agent: _AxAgentMixin,
        resolution: int = 41,
        update_interval_ms: int = 100,
    ) -> None:
        super().__init__()
        # Imported here (not at module scope) so that ax is only required when the
        # callback is actually constructed against an agent.
        from ax.core.parameter import ChoiceParameter, ParameterType, RangeParameter

        self._agent = agent
        self._resolution = resolution
        self._update_interval_ms = update_interval_ms

        self._lock = threading.Lock()
        self._version = 0
        self._observed: dict[str, list[Any]] = {}

        # Extract DOF metadata from the underlying Ax experiment.
        parameters = agent.ax_client._experiment.parameters
        self._param_info: dict[str, dict[str, Any]] = {}
        for name, parameter in parameters.items():
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
                    "values": [_py(v) for v in parameter.values],
                }
        self._dof_names = list(self._param_info.keys())

        # Extract objective (metric) names.
        opt_config = agent.ax_client._experiment.optimization_config
        if opt_config is None:
            raise ValueError("The agent's optimization has not been configured; no objectives to plot.")
        self._objective_names = list(opt_config.objective.metric_names)

    # -- Bluesky callback hooks ------------------------------------------------

    def start(self, doc: RunStart) -> None:
        """Bump the version so the app refreshes at the start of a run."""
        with self._lock:
            self._version += 1

    def event(self, doc: Event) -> Event:
        """Record observed DOF values and bump the version on each new trial."""
        data = doc.get("data", {})
        with self._lock:
            for name in self._dof_names:
                if name in data:
                    self._observed.setdefault(name, []).extend(_to_list(data[name]))
            self._version += 1
        return doc

    def stop(self, doc: RunStop) -> RunStop | None:
        """Bump the version at the end of a run."""
        with self._lock:
            self._version += 1
        return doc

    # -- Public accessors ------------------------------------------------------

    @property
    def dof_names(self) -> list[str]:
        """The names of the degrees of freedom available for the x/y axes."""
        return list(self._dof_names)

    @property
    def objective_names(self) -> list[str]:
        """The names of the objectives available to visualize."""
        return list(self._objective_names)

    @property
    def version(self) -> int:
        """A monotonically increasing counter that changes when new data arrives."""
        with self._lock:
            return self._version

    # -- Grid / figure construction --------------------------------------------

    def _axis_values(self, name: str, resolution: int) -> tuple[np.ndarray, bool]:
        """Return the sample values for an axis and whether it is discrete."""
        info = self._param_info[name]
        if info["kind"] == "range":
            values = np.linspace(info["lower"], info["upper"], resolution)
            if info["is_int"]:
                values = np.unique(np.round(values).astype(int))
            return values, bool(info["is_int"])
        return np.array(info["values"], dtype=object), True

    def _fixed_value(self, name: str) -> Any:
        """Return the held-fixed value for a DOF not on the x/y axes."""
        info = self._param_info[name]
        if info["kind"] == "range":
            mid = (info["lower"] + info["upper"]) / 2.0
            return int(round(mid)) if info["is_int"] else mid
        return info["values"][0]

    def _coerce(self, name: str, value: Any) -> Any:
        """Coerce a grid value to the DOF's expected Python type."""
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
        """Build a Plotly heatmap of the surrogate model's predicted mean.

        Parameters
        ----------
        x_name : str
            The DOF to place on the x axis.
        y_name : str
            The DOF to place on the y axis.
        objective_name : str
            The objective (metric) whose predicted mean is shown.
        resolution : int | None, optional
            Grid resolution per axis. Defaults to the value passed at construction.

        Returns
        -------
        plotly.graph_objects.Figure
            The heatmap figure. If the surrogate model cannot yet make predictions
            (e.g. too few trials), a figure with an explanatory annotation is returned.
        """
        import plotly.graph_objects as go

        resolution = resolution or self._resolution

        if x_name == y_name:
            return self._message_figure("Select two different DOFs for the x and y axes.")

        x_values, _ = self._axis_values(x_name, resolution)
        y_values, _ = self._axis_values(y_name, resolution)

        fixed = {name: self._fixed_value(name) for name in self._dof_names}
        points: list[dict[str, Any]] = []
        for y_val in y_values:
            for x_val in x_values:
                point = dict(fixed)
                point[x_name] = self._coerce(x_name, x_val)
                point[y_name] = self._coerce(y_name, y_val)
                points.append(point)

        try:
            predictions = self._agent.ax_client.predict(points)
        except Exception as exc:  # noqa: BLE001 - surface any model-not-ready error to the UI
            return self._message_figure(
                f"Surrogate model is not ready to predict yet.<br>Run more trials to continue.<br><br>({exc})"
            )

        try:
            z = np.array([pred[objective_name][0] for pred in predictions], dtype=float)
        except KeyError:
            return self._message_figure(f"Objective '{objective_name}' is not available in the model predictions.")

        z = z.reshape(len(y_values), len(x_values))

        figure = go.Figure(
            data=go.Heatmap(
                x=list(x_values),
                y=list(y_values),
                z=z,
                colorscale="Viridis",
                colorbar={"title": objective_name},
                hovertemplate=f"{x_name}: %{{x}}<br>{y_name}: %{{y}}<br>{objective_name}: %{{z:.4g}}<extra></extra>",
            )
        )

        # Overlay observed trials, if any.
        with self._lock:
            observed_x = list(self._observed.get(x_name, []))
            observed_y = list(self._observed.get(y_name, []))
        n_observed = min(len(observed_x), len(observed_y))
        if n_observed:
            figure.add_trace(
                go.Scatter(
                    x=observed_x[:n_observed],
                    y=observed_y[:n_observed],
                    mode="markers",
                    marker={"color": "white", "size": 7, "line": {"color": "black", "width": 1}},
                    name="observed",
                    hovertemplate=f"{x_name}: %{{x}}<br>{y_name}: %{{y}}<extra>observed</extra>",
                )
            )

        figure.update_layout(
            title=f"Surrogate mean of '{objective_name}'",
            xaxis_title=x_name,
            yaxis_title=y_name,
            margin={"l": 60, "r": 30, "t": 50, "b": 50},
            template="plotly_white",
        )
        return figure

    @staticmethod
    def _message_figure(message: str) -> go.Figure:
        """Return an empty figure displaying a centered message."""
        import plotly.graph_objects as go

        figure = go.Figure()
        figure.add_annotation(text=message, showarrow=False, font={"size": 14}, xref="paper", yref="paper", x=0.5, y=0.5)
        figure.update_layout(
            xaxis={"visible": False},
            yaxis={"visible": False},
            margin={"l": 20, "r": 20, "t": 20, "b": 20},
            template="plotly_white",
        )
        return figure

    # -- Dash app --------------------------------------------------------------

    def build_app(self, **dash_kwargs: Any):
        """Build and return a Dash app that renders the live surrogate heatmap.

        Parameters
        ----------
        **dash_kwargs : Any
            Additional keyword arguments forwarded to :class:`dash.Dash`.

        Returns
        -------
        dash.Dash
            The configured Dash application. Call ``app.run(...)`` to start it, or use
            :meth:`serve` for convenience.
        """
        from dash import Dash, Input, Output, dcc, html

        default_x = self._dof_names[0]
        default_y = self._dof_names[1] if len(self._dof_names) > 1 else self._dof_names[0]
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
                                    options=[{"label": n, "value": n} for n in self._dof_names],
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
                                    options=[{"label": n, "value": n} for n in self._dof_names],
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
                                    options=[{"label": n, "value": n} for n in self._objective_names],
                                    value=default_objective,
                                    id="surrogate-objective-dropdown",
                                    clearable=False,
                                ),
                            ],
                            style={"flex": "1", "padding": "0 8px"},
                        ),
                    ],
                    style={"display": "flex", "maxWidth": "900px", "margin": "0 auto"},
                ),
                dcc.Graph(id="surrogate-graph", style={"height": "70vh"}),
                dcc.Interval(id="surrogate-interval", interval=self._update_interval_ms, n_intervals=0),
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
            Input("surrogate-rendered-version", "data"),
        )
        def _update_graph(x_name, y_name, objective_name, _n_intervals, rendered_version):
            from dash import ctx, no_update

            current_version = self.version
            # On a timer tick, only recompute when new data has arrived. Dropdown
            # changes always force a recompute.
            triggered_by_timer = ctx.triggered_id == "surrogate-interval"
            if triggered_by_timer and current_version == rendered_version:
                return no_update, no_update

            figure = self.compute_figure(x_name, y_name, objective_name)
            return figure, current_version

        return app

    def serve(self, host: str = "127.0.0.1", port: int = 8050, debug: bool = False, **run_kwargs: Any) -> None:
        """Build the Dash app and start the development server (blocking).

        Parameters
        ----------
        host : str, optional
            The host interface to bind to. Default is ``"127.0.0.1"``.
        port : int, optional
            The port to serve on. Default is 8050.
        debug : bool, optional
            Whether to run Dash in debug mode. Default is False.
        **run_kwargs : Any
            Additional keyword arguments forwarded to ``dash.Dash.run``.

        Notes
        -----
        This call blocks. To run alongside a Bluesky ``RunEngine`` in the same process,
        start it in a background thread, e.g.::

            import threading
            threading.Thread(target=viz.serve, kwargs={"port": 8050}, daemon=True).start()
        """
        app = self.build_app()
        app.run(host=host, port=port, debug=debug, **run_kwargs)
>>>>>>> 484b29b8fca3b98b90ff71b7fcde88f9de297827
