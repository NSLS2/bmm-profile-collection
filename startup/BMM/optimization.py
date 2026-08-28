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
from blop.protocols import Actuator, Sensor
from bluesky.plan_stubs import null
from bluesky.preprocessors import finalize_wrapper
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

    def main_plan() -> MsgGenerator[dict[str, Any]]:
        energy_map: dict[str, Any] = {}
        resolved_resources.prompt_state.prompt = False

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

            if energy_map_filename is not None:
                with Path(energy_map_filename).open("wb") as stream:
                    pickle.dump(energy_map, stream)

        print(f"energy_map={energy_map}")
        return energy_map

    def cleanup_plan() -> MsgGenerator[None]:
        resolved_resources.prompt_state.prompt = previous_prompt
        yield from null()

    return (yield from finalize_wrapper(main_plan(), cleanup_plan()))
