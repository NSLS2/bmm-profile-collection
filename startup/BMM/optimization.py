from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from numbers import Integral, Real
from pathlib import Path
import pickle
import threading
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol, cast

from ax.api.protocols import IMetric
from blop.ax import (
    Agent,
    DOF,
    Objective,
    OutcomeConstraint,
    RangeDOF,
    ScalarizedObjective,
)
from blop.protocols import AcquisitionPlan, Actuator, EvaluationFunction, Sensor
from bluesky.callbacks import CallbackBase
from bluesky.plan_stubs import null
from bluesky.plans import count
from bluesky.preprocessors import finalize_wrapper, inject_md_wrapper
from bluesky.protocols import Readable
from bluesky.utils import MsgGenerator
from event_model import Event, RunStart, RunStop
import numpy as np
from skimage.filters import gaussian, threshold_otsu
from skimage.transform import resize
from tiled.client.container import Container

if TYPE_CHECKING:
    import plotly.graph_objects as go
    from matplotlib.figure import Figure

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
    x_crop: tuple[int, int] | None = None
    y_crop: tuple[int, int] | None = None
    blur_sigma: float | None = 2.0
    upscale_factor: int | None = 4


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
    fwhm_x: float
    fwhm_y: float
    centroid_x: float
    centroid_y: float

@dataclass(frozen=True)
class _ImageProcessingStage:
    name: str
    image: np.ndarray
    x_coordinates: np.ndarray
    y_coordinates: np.ndarray
    threshold: float | None = None


@dataclass(frozen=True)
class _ResolvedEnergyAlignmentUID:
    uid: str
    run: Any
    source_uid: str
    source_start: Mapping[str, Any]

@dataclass(frozen=True)
class _EnergyAlignmentDebugAcquisition:
    uid: str
    run: Any
    source_uid: str
    start: Mapping[str, Any]
    source_start: Mapping[str, Any]
    suggestions: tuple[Mapping[str, Any], ...]
    energies: tuple[float | None, ...]
    intensities: tuple[float, ...]
    event_count: int
    image_field: str
    intensity_field: str
    energy_field: str

@dataclass(frozen=True)
class _DebugPanelLimits:
    image_min: float
    image_max: float
    x_marginal_max: float
    y_marginal_max: float

@dataclass(frozen=True)
class _DebugFrameColumn:
    acquisition: _EnergyAlignmentDebugAcquisition
    suggestion_index: int
    event_index: int
    stages: tuple[_ImageProcessingStage, ...] | None
    error: str | None = None


_IMAGE_EVALUATION_OUTCOMES = frozenset(
    {"fwhm_x", "fwhm_y", "centroid_x", "centroid_y", "centroid_distance", "intensity"}
)

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
    x_crop=(900, 1040),
    y_crop=None,
)

PER_ENERGY_ALIGNMENT = EnergyAlignmentProfile(
    name="per-energy-alignment",
    sensors=("camera", "i0"),
    dofs=_ALIGNMENT_DOFS,
    objectives=(Objective(name="centroid_distance", minimize=True),),
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


def _validate_evaluation_config(parameters: BeamEvaluationConfig) -> None:
    for axis, bounds in (("x", parameters.x_crop), ("y", parameters.y_crop)):
        if bounds is None:
            continue
        if (
            not isinstance(bounds, tuple)
            or len(bounds) != 2
            or any(
                isinstance(bound, bool) or not isinstance(bound, Integral)
                for bound in bounds
            )
            or bounds[0] < 0
            or bounds[0] >= bounds[1]
        ):
            raise ValueError(f"Invalid {axis} crop {bounds!r}")

    blur_sigma = parameters.blur_sigma
    if blur_sigma is not None and (
        isinstance(blur_sigma, bool)
        or not isinstance(blur_sigma, Real)
        or not np.isfinite(blur_sigma)
        or blur_sigma <= 0
    ):
        raise ValueError("blur_sigma must be finite and positive")

    upscale_factor = parameters.upscale_factor
    if upscale_factor is not None and (
        isinstance(upscale_factor, bool)
        or not isinstance(upscale_factor, Integral)
        or upscale_factor < 2
    ):
        raise ValueError("upscale_factor must be a non-boolean integer of at least 2")


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

    _validate_evaluation_config(profile.evaluation)

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


def _image_processing_stages(
    image: np.ndarray,
    parameters: BeamEvaluationConfig,
) -> tuple[_ImageProcessingStage, ...]:
    _validate_evaluation_config(parameters)

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
    if not np.isfinite(gray).all() or np.any(gray < 0):
        raise ValueError("Image contains non-finite or negative pixels")

    height, width = gray.shape
    x_coordinates = np.arange(width, dtype=np.float64)
    y_coordinates = np.arange(height, dtype=np.float64)
    stages = [
        _ImageProcessingStage("grayscale", gray, x_coordinates, y_coordinates)
    ]

    x_start, x_stop = parameters.x_crop or (0, width)
    if x_stop > width:
        raise ValueError(
            f"Invalid x crop {parameters.x_crop!r} for image width {width}"
        )
    y_start, y_stop = parameters.y_crop or (0, height)
    if y_stop > height:
        raise ValueError(
            f"Invalid y crop {parameters.y_crop!r} for image height {height}"
        )

    cropped = gray[y_start:y_stop, x_start:x_stop]
    x_coordinates = x_coordinates[x_start:x_stop]
    y_coordinates = y_coordinates[y_start:y_stop]
    if parameters.x_crop is not None or parameters.y_crop is not None:
        stages.append(
            _ImageProcessingStage(
                "crop", cropped, x_coordinates, y_coordinates
            )
        )

    if parameters.blur_sigma is None:
        filtered = cropped
    else:
        filtered = gaussian(
            cropped,
            sigma=parameters.blur_sigma,
            mode="reflect",
            preserve_range=True,
            channel_axis=None,
        )
        stages.append(
            _ImageProcessingStage(
                f"Gaussian σ={parameters.blur_sigma}",
                filtered,
                x_coordinates,
                y_coordinates,
            )
        )

    threshold = float(threshold_otsu(filtered))
    thresholded = np.where(filtered > threshold, filtered, 0.0)
    if not np.any(thresholded > 0):
        raise ValueError("Image has no positive signal after Otsu thresholding")
    stages.append(
        _ImageProcessingStage(
            "Otsu threshold",
            thresholded,
            x_coordinates,
            y_coordinates,
            threshold,
        )
    )

    if parameters.upscale_factor is not None:
        scale = int(parameters.upscale_factor)
        processed = resize(
            thresholded,
            (cropped.shape[0] * scale, cropped.shape[1] * scale),
            order=3,
            mode="reflect",
            clip=True,
            preserve_range=True,
            anti_aliasing=False,
        )
        x_coordinates = (
            x_start + (np.arange(processed.shape[1]) + 0.5) / scale - 0.5
        )
        y_coordinates = (
            y_start + (np.arange(processed.shape[0]) + 0.5) / scale - 0.5
        )
        stages.append(
            _ImageProcessingStage(
                f"resize ×{scale}", processed, x_coordinates, y_coordinates
            )
        )

    return tuple(stages)


def _preprocess_image(
    image: np.ndarray,
    parameters: BeamEvaluationConfig,
) -> _ImageProcessingStage:
    return _image_processing_stages(image, parameters)[-1]


def _full_width_half_maximum(
    profile: np.ndarray,
    coordinates: np.ndarray,
    *,
    axis: str,
) -> float:
    profile = np.asarray(profile, dtype=np.float64)
    coordinates = np.asarray(coordinates, dtype=np.float64)
    error = f"Cannot compute {axis} FWHM: peak is not bracketed at half maximum"
    if (
        profile.ndim != 1
        or coordinates.ndim != 1
        or profile.shape != coordinates.shape
        or profile.size < 3
        or not np.isfinite(profile).all()
        or not np.isfinite(coordinates).all()
        or not np.all(np.diff(coordinates) > 0)
    ):
        raise ValueError(error)

    peak_index = int(np.argmax(profile))
    peak = profile[peak_index]
    if peak <= 0:
        raise ValueError(error)
    half_maximum = peak / 2

    left_below = np.flatnonzero(profile[:peak_index] < half_maximum)
    right_below = np.flatnonzero(profile[peak_index + 1 :] < half_maximum)
    if left_below.size == 0 or right_below.size == 0:
        raise ValueError(error)

    left_outer = int(left_below[-1])
    left_inner = left_outer + 1
    if profile[left_inner] == half_maximum:
        left_crossing = coordinates[left_inner]
    else:
        left_crossing = coordinates[left_outer] + (
            (half_maximum - profile[left_outer])
            * (coordinates[left_inner] - coordinates[left_outer])
            / (profile[left_inner] - profile[left_outer])
        )

    right_outer = peak_index + 1 + int(right_below[0])
    right_inner = right_outer - 1
    if profile[right_inner] == half_maximum:
        right_crossing = coordinates[right_inner]
    else:
        right_crossing = coordinates[right_inner] + (
            (half_maximum - profile[right_inner])
            * (coordinates[right_outer] - coordinates[right_inner])
            / (profile[right_outer] - profile[right_inner])
        )

    width = float(right_crossing - left_crossing)
    if not np.isfinite(width) or width <= 0:
        raise ValueError(error)
    return width


def _compute_processed_image_stats(
    image: np.ndarray,
    x_coordinates: np.ndarray,
    y_coordinates: np.ndarray,
) -> BeamStats:
    x_profile = image.sum(axis=0)
    y_profile = image.sum(axis=1)
    x_mass = float(x_profile.sum())
    y_mass = float(y_profile.sum())
    if not np.isfinite(x_mass) or not np.isfinite(y_mass) or x_mass <= 0 or y_mass <= 0:
        raise ValueError("Image has no positive signal after Otsu thresholding")

    centroid_x = float(np.dot(x_profile, x_coordinates) / x_mass)
    centroid_y = float(np.dot(y_profile, y_coordinates) / y_mass)
    if not np.isfinite(centroid_x) or not np.isfinite(centroid_y):
        raise ValueError("Image centroids must be finite")

    return BeamStats(
        fwhm_x=_full_width_half_maximum(x_profile, x_coordinates, axis="x"),
        fwhm_y=_full_width_half_maximum(y_profile, y_coordinates, axis="y"),
        centroid_x=centroid_x,
        centroid_y=centroid_y,
    )


def compute_image_stats(
    image: np.ndarray,
    parameters: BeamEvaluationConfig,
) -> BeamStats:
    """Compute beam widths and centroids from one camera image."""
    processed = _preprocess_image(image, parameters)
    return _compute_processed_image_stats(
        processed.image,
        processed.x_coordinates,
        processed.y_coordinates,
    )


def compute_multi_energy_alignment_metrics(
    reference_image: np.ndarray,
    images: Sequence[np.ndarray] | np.ndarray,
    intensities: Sequence[Any] | np.ndarray,
    parameters: BeamEvaluationConfig,
) -> dict[str, float]:
    """Compute horizontal beam-stability metrics for already-acquired images."""
    reference_stats = compute_image_stats(reference_image, parameters)
    image_stats = [compute_image_stats(image, parameters) for image in images]

    centroid_offsets = np.array(
        [stats.centroid_x - reference_stats.centroid_x for stats in image_stats]
    )
    fwhm_values = np.array([stats.fwhm_x for stats in image_stats])
    intensity_values = np.asarray(intensities).squeeze()

    centroid_x_rmse_px = np.sqrt(np.mean(centroid_offsets**2))
    fwhm_x_rms_px = np.sqrt(np.mean(fwhm_values**2))

    return {
        "centroid_x_offset_mean_px": float(np.mean(centroid_offsets)),
        "centroid_x_std_px": float(np.std(centroid_offsets)),
        "centroid_x_span_px": float(
            np.max(centroid_offsets) - np.min(centroid_offsets)
        ),
        "centroid_x_rmse_px": float(centroid_x_rmse_px),
        "fwhm_x_mean_px": float(np.mean(fwhm_values)),
        "fwhm_x_std_px": float(np.std(fwhm_values)),
        "fwhm_x_rms_px": float(fwhm_x_rms_px),
        "fwhm_x_rms_normalized": float(fwhm_x_rms_px / reference_stats.fwhm_x),
        "intensity_min": float(np.min(intensity_values)),
        "intensity_mean": float(np.mean(intensity_values)),
    }


def compute_multi_energy_alignment_metrics_from_catalog(
    catalog: Container | Mapping[str, Any],
    reference_uid: str,
    per_energy_uids: Sequence[str],
    parameters: BeamEvaluationConfig,
) -> dict[str, float]:
    """Read completed per-energy runs and compute beam-stability metrics."""
    reference_run = catalog[reference_uid]
    reference_image = reference_run["primary"]["data"][parameters.image_field].read()
    images = []
    intensities = []
    for uid in per_energy_uids:
        data = catalog[uid]["primary"]["data"]
        images.append(data[parameters.image_field].read())
        intensities.append(data[parameters.intensity_field].read())

    return compute_multi_energy_alignment_metrics(
        reference_image,
        images,
        intensities,
        parameters,
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
    data = run["primary"]["data"]
    image = data[evaluation.image_field].read()
    intensity_data = np.asarray(data[evaluation.intensity_field].read())
    scalar_intensity = intensity_data.squeeze()
    if scalar_intensity.ndim != 0:
        raise ValueError(
            f"{evaluation.intensity_field!r} data must contain one finite scalar; "
            f"received shape {intensity_data.shape!r}"
        )
    try:
        intensity = float(scalar_intensity)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{evaluation.intensity_field!r} data must contain one finite scalar"
        ) from exc
    if not np.isfinite(intensity):
        raise ValueError(
            f"{evaluation.intensity_field!r} data must contain one finite scalar"
        )

    stats = compute_image_stats(image, evaluation)
    print(
        f"fwhm_x={stats.fwhm_x}, "
        f"fwhm_y={stats.fwhm_y}, "
        f"centroid_x={stats.centroid_x}, "
        f"centroid_y={stats.centroid_y}, "
        f"intensity={intensity}"
    )
    return stats

def _validate_pixel_size_um(
    pixel_size_um: tuple[float, float],
) -> tuple[float, float]:
    if (
        not isinstance(pixel_size_um, tuple)
        or len(pixel_size_um) != 2
        or any(
            isinstance(scale, bool)
            or not isinstance(scale, Real)
            or not np.isfinite(scale)
            or scale <= 0
            for scale in pixel_size_um
        )
    ):
        raise ValueError(
            "pixel_size_um must be a finite positive "
            "(x_um_per_pixel, y_um_per_pixel) tuple"
        )
    return float(pixel_size_um[0]), float(pixel_size_um[1])


def show_energy_alignment_debug(
    uids: str | Sequence[str],
    *,
    pixel_size_um: tuple[float, float],
    energy_field: str = "dcm_energy",
    profile: str | EnergyAlignmentProfile = PER_ENERGY_ALIGNMENT.name,
    resources: EnergyAlignmentResources | None = None,
) -> Figure:
    """Show image-processing diagnostics for completed alignment acquisitions.

    Beamline usage is ``show_energy_alignment_debug(outer_or_acquisition_uids,
    pixel_size_um=(x_scale, y_scale))``. One direct acquisition or outer Blop
    optimization UID renders its per-energy acquisition grid. An ordered
    sequence spanning multiple per-energy runs adds an ``all energies`` overlay
    built from those runs in caller order. Set ``energy_field`` when a run's
    recorded energy signal is not ``dcm_energy``.

    This viewer only reads completed runs from the configured Tiled catalog. It
    does not validate, read, or move profile actuators or sensors.
    """
    pixel_size = _validate_pixel_size_um(pixel_size_um)
    resolved_profile = get_energy_alignment_profile(profile)
    resolved_resources = _resolve_resources(resources)
    resolved_uids = _resolve_energy_alignment_debug_uids(
        uids,
        catalog=resolved_resources.catalog,
        image_field=resolved_profile.evaluation.image_field,
    )
    acquisitions = tuple(
        _describe_energy_alignment_acquisition(
            resolved_uid,
            evaluation=resolved_profile.evaluation,
            energy_field=energy_field,
        )
        for resolved_uid in resolved_uids
    )
    source_count = len({resolved_uid.source_uid for resolved_uid in resolved_uids})

    from matplotlib import pyplot as plt

    renderer = (
        _render_multi_energy_debug if source_count > 1 else _render_per_energy_debug
    )
    figure = renderer(
        acquisitions,
        profile=resolved_profile,
        pixel_size_um=pixel_size,
        pyplot=plt,
    )
    plt.show(block=False)
    return figure


class ImageEvaluation:
    """Evaluate camera images against a reference beam position.

    Each completed run provides one image and one finite scalar intensity per
    suggestion. Batch image data uses its leading dimension as acquisition
    order, the squeezed intensity data has shape ``(suggestion_count,)``, and
    ``start.blop_suggestions`` contains the unique suggestion IDs in that same
    order. Single acquisitions may omit this metadata and may include singleton
    dimensions around their image or intensity value.
    """

    def __init__(
        self,
        tiled_client: Container,
        reference_scan_uid: str,
        parameters: BeamEvaluationConfig,
    ):
        self.tiled_client = tiled_client
        self.reference_scan_uid = reference_scan_uid
        self.parameters = parameters
        reference_run = self.tiled_client[reference_scan_uid]
        reference_image = reference_run["primary"]["data"][
            parameters.image_field
        ].read()
        reference_stats = compute_image_stats(reference_image, parameters)
        self.reference_centroid_x = reference_stats.centroid_x
        self.reference_centroid_y = reference_stats.centroid_y

    def __call__(self, uid: str, suggestions: list[dict]) -> list[dict]:
        if not suggestions:
            return []

        count = len(suggestions)
        run = self.tiled_client[uid]
        data = run["primary"]["data"]
        acquired_images = np.asarray(data[self.parameters.image_field].read())
        intensity_data = np.asarray(data[self.parameters.intensity_field].read())

        def shape_error(field: str, values: np.ndarray) -> ValueError:
            return ValueError(
                f"Received {count} suggestions but {field!r} data has shape "
                f"{values.shape!r}"
            )

        if count == 1:
            images = (acquired_images,)
        else:
            if acquired_images.ndim == 0 or acquired_images.shape[0] != count:
                raise shape_error(self.parameters.image_field, acquired_images)
            images = acquired_images

        try:
            intensities = np.atleast_1d(
                intensity_data.astype(np.float64, copy=False).squeeze()
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{self.parameters.intensity_field!r} data must be numeric"
            ) from exc
        if intensities.shape != (count,):
            raise shape_error(self.parameters.intensity_field, intensity_data)
        if not np.isfinite(intensities).all():
            raise ValueError(
                f"{self.parameters.intensity_field!r} data must contain finite values"
            )

        suggestion_ids = [suggestion["_id"] for suggestion in suggestions]
        if count == 1:
            acquired_ids = suggestion_ids
        else:
            try:
                acquired_ids = [
                    suggestion["_id"]
                    for suggestion in run.metadata["start"]["blop_suggestions"]
                ]
            except (AttributeError, KeyError, TypeError) as exc:
                raise ValueError(
                    "Batch evaluation requires blop_suggestions metadata"
                ) from exc
            if len(acquired_ids) != count or set(acquired_ids) != set(suggestion_ids):
                raise ValueError(
                    "blop_suggestions metadata IDs do not match supplied suggestion IDs"
                )

        paired_by_id = {
            suggestion_id: (image, float(intensity))
            for suggestion_id, image, intensity in zip(
                acquired_ids, images, intensities, strict=True
            )
        }
        outcomes = []
        for suggestion in suggestions:
            image, intensity = paired_by_id[suggestion["_id"]]
            stats = compute_image_stats(image, self.parameters)
            outcomes.append(
                {
                    "_id": suggestion["_id"],
                    "fwhm_x": float(stats.fwhm_x),
                    "fwhm_y": float(stats.fwhm_y),
                    "centroid_x": float(stats.centroid_x),
                    "centroid_y": float(stats.centroid_y),
                    "centroid_distance": float(
                        np.hypot(
                            self.reference_centroid_x - stats.centroid_x,
                            self.reference_centroid_y - stats.centroid_y,
                        )
                    ),
                    "intensity": intensity,
                }
            )
        return outcomes


def acquire_target_position(sensors: Sequence[Readable]) -> MsgGenerator[str]:
    """Record supplied sensor values and DOF positions at the target state."""
    return (
        yield from count(
            sensors,
            num=1,
            md={"plan_name": "acquire_target_position"},
        )
    )




def make_energy_alignment_agent(
    reference_scan_uid: str,
    *,
    profile: str | EnergyAlignmentProfile = PER_ENERGY_ALIGNMENT.name,
    resources: EnergyAlignmentResources | None = None,
    evaluation_function: EvaluationFunction | None = None,
    acquisition_plan: AcquisitionPlan | None = None,
    subscribe_to_dash: bool = True,
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

    if subscribe_to_dash:
        subscribe_dash_to_agent(agent)

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
    *,
    reference_scan_uid: str | None = None,
    energy_map_filename: str | Path | None = None,
    profile: str | EnergyAlignmentProfile = PER_ENERGY_ALIGNMENT.name,
    resources: EnergyAlignmentResources | None = None,
) -> MsgGenerator[dict[str, Any]]:
    """Optimize each energy against a supplied or newly acquired target run."""
    resolved_profile = get_energy_alignment_profile(profile)
    resolved_resources = _resolve_resources(resources)
    _validate_resources(resolved_resources, resolved_profile)
    previous_prompt = resolved_resources.prompt_state.prompt

    def main_plan() -> MsgGenerator[dict[str, Any]]:
        energy_map: dict[str, Any] = {}
        if not energies:
            return energy_map

        target_uid = reference_scan_uid
        if target_uid is None:
            target_readables: list[Readable] = [
                resolved_resources.sensors[name] for name in resolved_profile.sensors
            ]
            target_readables.extend(
                cast(Readable, dof.actuator)
                for dof in _bind_dofs(resolved_resources, resolved_profile)
            )
            target_uid = yield from acquire_target_position(target_readables)
        evaluation_function = ImageEvaluation(
            resolved_resources.catalog,
            reference_scan_uid=target_uid,
            parameters=resolved_profile.evaluation,
        )
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
                target_uid,
                profile=resolved_profile,
                resources=resolved_resources,
                evaluation_function=evaluation_function,
            )
            optimize_plan = optimization_metadata_wrapper(
                agent.optimize(resolved_profile.optimization.iterations),
                energy,
                target_uid,
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


# --------- Dash live plotting registration ---------

def subscribe_dash_to_agent(agent):
    try:
        viz = SurrogateModelDashCallback(agent, resolution=41)
        agent.subscribe(viz)

        # run alongside the RunEngine
        threading.Thread(target=viz.serve, kwargs={"port": 8050}, daemon=True).start()
    except Exception as e:
        print(f"Failed to register agent to dash callback with error {e}")


# --------- Acquired-image debug viewer ---------

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

def _run_start_metadata(run: Any) -> Mapping[str, Any]:
    metadata = getattr(run, "metadata", {})
    if not isinstance(metadata, Mapping):
        return {}
    start = metadata.get("start", {})
    return start if isinstance(start, Mapping) else {}


def _complete_run_uid(run: Any, requested_uid: str) -> str:
    uid = _py(_run_start_metadata(run).get("uid", requested_uid))
    return uid if isinstance(uid, str) and uid else requested_uid


def _energy_alignment_debug_data(run: Any, uid: str) -> Any:
    try:
        return run["primary"]["data"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"UID {uid!r} has no primary data") from exc


def _resolve_energy_alignment_debug_uids(
    uids: str | Sequence[str],
    *,
    catalog: Any,
    image_field: str,
) -> tuple[_ResolvedEnergyAlignmentUID, ...]:
    if isinstance(uids, str):
        requested_uids = [uids]
    elif isinstance(uids, Sequence):
        requested_uids = list(uids)
    else:
        raise ValueError("uids must be a non-empty string or sequence of strings")
    if not requested_uids:
        raise ValueError("uids must contain at least one non-empty string")
    for index, uid in enumerate(requested_uids):
        if not isinstance(uid, str) or not uid:
            raise ValueError(
                f"uids[{index}] must be a non-empty string; received {uid!r}"
            )

    resolved: list[_ResolvedEnergyAlignmentUID] = []
    seen_direct_uids: set[str] = set()
    seen_requested_uids: set[str] = set()

    def append_direct(
        requested_uid: str,
        run: Any,
        *,
        source_uid: str,
        source_start: Mapping[str, Any],
    ) -> None:
        direct_uid = _complete_run_uid(run, requested_uid)
        if direct_uid in seen_direct_uids:
            return
        data = _energy_alignment_debug_data(run, direct_uid)
        if image_field not in data:
            raise ValueError(
                f"UID {direct_uid!r} does not contain image field "
                f"{image_field!r}"
            )
        seen_direct_uids.add(direct_uid)
        resolved.append(
            _ResolvedEnergyAlignmentUID(
                uid=direct_uid,
                run=run,
                source_uid=source_uid,
                source_start=source_start,
            )
        )

    for requested_uid in requested_uids:
        if requested_uid in seen_requested_uids:
            continue
        seen_requested_uids.add(requested_uid)
        run = catalog[requested_uid]
        complete_uid = _complete_run_uid(run, requested_uid)
        data = _energy_alignment_debug_data(run, complete_uid)
        start = _run_start_metadata(run)
        if image_field in data:
            append_direct(
                requested_uid,
                run,
                source_uid=complete_uid,
                source_start=start,
            )
            continue
        if "acquisition_uid" not in data:
            raise ValueError(
                f"UID {complete_uid!r} contains neither image field "
                f"{image_field!r} nor 'acquisition_uid'"
            )

        linked_uids = [
            _py(value) for value in _to_list(data["acquisition_uid"].read())
        ]
        if not linked_uids:
            raise ValueError(
                f"UID {complete_uid!r} field 'acquisition_uid' contains no values"
            )
        for index, linked_uid in enumerate(linked_uids):
            if not isinstance(linked_uid, str) or not linked_uid:
                raise ValueError(
                    f"UID {complete_uid!r} field 'acquisition_uid' contains "
                    f"an invalid value at index {index}: {linked_uid!r}"
                )
            if linked_uid in seen_direct_uids:
                continue
            linked_run = catalog[linked_uid]
            append_direct(
                linked_uid,
                linked_run,
                source_uid=complete_uid,
                source_start=start,
            )

    return tuple(resolved)

def _numeric_debug_samples(value: Any, *, uid: str, field: str) -> tuple[float, ...]:
    raw = np.asarray(value)
    if np.iscomplexobj(raw) or np.issubdtype(raw.dtype, np.bool_):
        raise ValueError(
            f"UID {uid!r} field {field!r} must contain numeric scalar samples"
        )
    try:
        samples = np.atleast_1d(raw.astype(np.float64, copy=False).squeeze())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"UID {uid!r} field {field!r} must contain numeric scalar samples"
        ) from exc
    if samples.ndim != 1:
        raise ValueError(
            f"UID {uid!r} field {field!r} must contain scalar samples; "
            f"received shape {raw.shape!r}"
        )
    if not np.isfinite(samples).all():
        raise ValueError(
            f"UID {uid!r} field {field!r} must contain finite values"
        )
    return tuple(float(sample) for sample in samples)


def _describe_energy_alignment_acquisition(
    resolved_uid: _ResolvedEnergyAlignmentUID,
    *,
    evaluation: BeamEvaluationConfig,
    energy_field: str,
) -> _EnergyAlignmentDebugAcquisition:
    uid = resolved_uid.uid
    run = resolved_uid.run
    data = _energy_alignment_debug_data(run, uid)
    start = _run_start_metadata(run)

    if "blop_suggestions" not in start:
        suggestions: tuple[Mapping[str, Any], ...] = ({},)
    else:
        suggestion_values = _to_list(start["blop_suggestions"])
        if not suggestion_values:
            raise ValueError(
                f"UID {uid!r} metadata field 'start.blop_suggestions' "
                "must contain at least one suggestion"
            )
        invalid_index = next(
            (
                index
                for index, suggestion in enumerate(suggestion_values)
                if not isinstance(suggestion, Mapping)
            ),
            None,
        )
        if invalid_index is not None:
            raise ValueError(
                f"UID {uid!r} metadata field 'start.blop_suggestions' "
                f"contains a non-mapping value at index {invalid_index}"
            )
        suggestions = tuple(suggestion_values)

    suggestion_count = len(suggestions)
    if energy_field in data:
        energies = _numeric_debug_samples(
            data[energy_field].read(), uid=uid, field=energy_field
        )
        if len(energies) != suggestion_count:
            raise ValueError(
                f"UID {uid!r} field {energy_field!r} has {len(energies)} "
                f"samples for {suggestion_count} suggestions; per-energy debug "
                "requires one energy per suggestion. Supply multiple per-energy "
                "run UIDs instead of a scan-shaped acquisition."
            )
    else:
        energies = tuple(None for _ in suggestions)
    event_count = suggestion_count

    if evaluation.intensity_field not in data:
        raise ValueError(
            f"UID {uid!r} does not contain intensity field "
            f"{evaluation.intensity_field!r}"
        )
    intensities = _numeric_debug_samples(
        data[evaluation.intensity_field].read(),
        uid=uid,
        field=evaluation.intensity_field,
    )
    if len(intensities) != event_count:
        raise ValueError(
            f"UID {uid!r} field {evaluation.intensity_field!r} has "
            f"{len(intensities)} samples; expected {event_count} to match "
            "the per-energy suggestion count"
        )

    return _EnergyAlignmentDebugAcquisition(
        uid=uid,
        run=run,
        source_uid=resolved_uid.source_uid,
        start=start,
        source_start=resolved_uid.source_start,
        suggestions=suggestions,
        energies=energies,
        intensities=intensities,
        event_count=event_count,
        image_field=evaluation.image_field,
        intensity_field=evaluation.intensity_field,
        energy_field=energy_field,
    )


def _read_energy_alignment_debug_frames(
    acquisition: _EnergyAlignmentDebugAcquisition,
) -> np.ndarray:
    data = _energy_alignment_debug_data(acquisition.run, acquisition.uid)
    raw = np.asarray(data[acquisition.image_field].read())
    if acquisition.event_count == 1:
        image = raw.squeeze()
        if image.ndim == 2 or (image.ndim == 3 and image.shape[-1] in (3, 4)):
            return image[np.newaxis, ...]
    elif raw.ndim >= 3 and raw.shape[0] == acquisition.event_count:
        return raw

    detail = (
        f"expected {acquisition.event_count} frames for "
        f"{len(acquisition.suggestions)} per-energy suggestions; scan-shaped "
        "acquisitions are unsupported, so supply multiple per-energy run UIDs"
    )
    raise ValueError(
        f"UID {acquisition.uid!r} field {acquisition.image_field!r} has shape "
        f"{raw.shape!r}; {detail}"
    )

def _debug_panel_limits(
    stages: Sequence[_ImageProcessingStage],
) -> _DebugPanelLimits | None:
    if not stages:
        return None
    image_min = min(float(np.min(stage.image)) for stage in stages)
    image_max = max(float(np.max(stage.image)) for stage in stages)
    if image_max <= image_min:
        image_max = image_min + 1.0
    x_marginal_max = max(
        float(np.max(stage.image.sum(axis=0))) for stage in stages
    )
    y_marginal_max = max(
        float(np.max(stage.image.sum(axis=1))) for stage in stages
    )
    return _DebugPanelLimits(
        image_min=image_min,
        image_max=image_max,
        x_marginal_max=max(x_marginal_max, 1.0),
        y_marginal_max=max(y_marginal_max, 1.0),
    )


def _coordinate_extent(coordinates: np.ndarray) -> tuple[float, float]:
    if coordinates.size > 1:
        spacing = float(coordinates[1] - coordinates[0])
    else:
        spacing = 1.0
    return (
        float(coordinates[0] - spacing / 2),
        float(coordinates[-1] + spacing / 2),
    )


def _render_energy_alignment_debug_panel(
    figure: Figure,
    cell: Any,
    *,
    stage: _ImageProcessingStage | None,
    pixel_size_um: tuple[float, float],
    row_label: str = "",
    column_label: str = "",
    final_stage: bool = False,
    limits: _DebugPanelLimits | None = None,
    overlay: Sequence[tuple[str, _ImageProcessingStage]] = (),
    error: str | None = None,
    pyplot: Any,
) -> tuple[Any, Any, Any]:
    inner = cell.subgridspec(
        2,
        2,
        height_ratios=(1, 4),
        width_ratios=(4, 1),
        hspace=0.05,
        wspace=0.05,
    )
    image_axis = figure.add_subplot(inner[1, 0])
    x_axis = figure.add_subplot(inner[0, 0], sharex=image_axis)
    y_axis = figure.add_subplot(inner[1, 1], sharey=image_axis)
    image_axis.set_gid("energy-alignment-image")
    x_axis.set_gid("energy-alignment-x-marginal")
    y_axis.set_gid("energy-alignment-y-marginal")
    x_axis.set_title(column_label, fontsize=8)

    if error is not None:
        x_axis.set_axis_off()
        y_axis.set_axis_off()
        image_axis.set_axis_off()
        image_axis.text(
            0.5,
            0.5,
            error,
            ha="center",
            va="center",
            wrap=True,
            transform=image_axis.transAxes,
        )
        if row_label:
            image_axis.text(
                0.0,
                1.02,
                row_label,
                ha="left",
                va="bottom",
                transform=image_axis.transAxes,
            )
        return image_axis, x_axis, y_axis

    x_scale, y_scale = pixel_size_um
    if stage is not None:
        if overlay:
            raise ValueError("A debug panel cannot be both ordinary and overlaid")
        x_coordinates = stage.x_coordinates * x_scale
        y_coordinates = stage.y_coordinates * y_scale
        x_limits = _coordinate_extent(x_coordinates)
        y_limits = _coordinate_extent(y_coordinates)
        panel_limits = limits or _debug_panel_limits((stage,))
        assert panel_limits is not None
        image_axis.imshow(
            stage.image,
            cmap="viridis",
            origin="lower",
            aspect="auto",
            extent=(*x_limits, *y_limits),
            vmin=panel_limits.image_min,
            vmax=panel_limits.image_max,
        )
        x_profile = stage.image.sum(axis=0)
        y_profile = stage.image.sum(axis=1)
        x_axis.plot(x_coordinates, x_profile)
        y_axis.plot(y_profile, y_coordinates)
        x_axis.set_ylim(0, panel_limits.x_marginal_max * 1.05)
        y_axis.set_xlim(0, panel_limits.y_marginal_max * 1.05)

        annotations = []
        if stage.threshold is not None:
            annotations.append(f"threshold = {stage.threshold:.6g}")
        if final_stage:
            try:
                stats = _compute_processed_image_stats(
                    stage.image,
                    stage.x_coordinates,
                    stage.y_coordinates,
                )
            except ValueError as exc:
                annotations.append(str(exc))
            else:
                image_axis.plot(
                    stats.centroid_x * x_scale,
                    stats.centroid_y * y_scale,
                    marker="+",
                    markersize=10,
                    markeredgewidth=1.5,
                    color="white",
                )
                annotations.extend(
                    (
                        f"centroid x = {stats.centroid_x:.3g} px = "
                        f"{stats.centroid_x * x_scale:.3g} µm",
                        f"centroid y = {stats.centroid_y:.3g} px = "
                        f"{stats.centroid_y * y_scale:.3g} µm",
                        f"FWHM x = {stats.fwhm_x:.3g} px = "
                        f"{stats.fwhm_x * x_scale:.3g} µm",
                        f"FWHM y = {stats.fwhm_y:.3g} px = "
                        f"{stats.fwhm_y * y_scale:.3g} µm",
                    )
                )
        if annotations:
            image_axis.text(
                0.02,
                0.98,
                "\n".join(annotations),
                ha="left",
                va="top",
                fontsize=6,
                color="white",
                bbox={"facecolor": "black", "alpha": 0.55, "pad": 2},
                transform=image_axis.transAxes,
            )
    elif overlay:
        colors = pyplot.get_cmap("viridis")(
            np.linspace(0.0, 1.0, len(overlay))
        )
        x_min = y_min = np.inf
        x_max = y_max = -np.inf
        threshold_labels = []
        for (label, overlay_stage), color in zip(overlay, colors, strict=True):
            peak = float(np.max(overlay_stage.image))
            normalized = overlay_stage.image / peak
            x_coordinates = overlay_stage.x_coordinates * x_scale
            y_coordinates = overlay_stage.y_coordinates * y_scale
            x_extent = _coordinate_extent(x_coordinates)
            y_extent = _coordinate_extent(y_coordinates)
            x_min, x_max = min(x_min, x_extent[0]), max(x_max, x_extent[1])
            y_min, y_max = min(y_min, y_extent[0]), max(y_max, y_extent[1])
            image_axis.contour(
                x_coordinates,
                y_coordinates,
                normalized,
                levels=(0.25, 0.5, 0.75),
                colors=[color],
            )
            x_profile = normalized.sum(axis=0)
            y_profile = normalized.sum(axis=1)
            x_profile /= np.max(x_profile)
            y_profile /= np.max(y_profile)
            x_axis.plot(x_coordinates, x_profile, color=color, label=label)
            y_axis.plot(y_profile, y_coordinates, color=color, label=label)
            image_axis.plot([], [], color=color, label=label)
            if overlay_stage.threshold is not None:
                threshold_labels.append(
                    f"{label}: threshold = {overlay_stage.threshold:.6g}"
                )
        image_axis.set_xlim(x_min, x_max)
        image_axis.set_ylim(y_min, y_max)
        x_axis.set_ylim(0, 1.05)
        y_axis.set_xlim(0, 1.05)
        image_axis.legend(title="energy", fontsize=6, title_fontsize=6)
        if threshold_labels:
            image_axis.text(
                0.02,
                0.02,
                "\n".join(threshold_labels),
                ha="left",
                va="bottom",
                fontsize=6,
                bbox={"facecolor": "white", "alpha": 0.7, "pad": 2},
                transform=image_axis.transAxes,
            )
    else:
        raise ValueError("A debug panel requires a stage, overlay, or error")

    image_axis.set_xlabel("x (µm)")
    image_axis.set_ylabel(f"{row_label}\ny (µm)" if row_label else "y (µm)")
    x_axis.set_ylabel("Σy")
    y_axis.set_xlabel("Σx")
    x_axis.tick_params(labelbottom=False)
    y_axis.tick_params(labelleft=False)
    return image_axis, x_axis, y_axis


def _configured_image_stage_names(
    parameters: BeamEvaluationConfig,
) -> tuple[str, ...]:
    names = ["grayscale"]
    if parameters.x_crop is not None or parameters.y_crop is not None:
        names.append("crop")
    if parameters.blur_sigma is not None:
        names.append(f"Gaussian σ={parameters.blur_sigma}")
    names.append("Otsu threshold")
    if parameters.upscale_factor is not None:
        names.append(f"resize ×{parameters.upscale_factor}")
    return tuple(names)


def _process_energy_alignment_debug_frame(
    acquisition: _EnergyAlignmentDebugAcquisition,
    image: np.ndarray,
    *,
    suggestion_index: int,
    event_index: int,
    parameters: BeamEvaluationConfig,
) -> _DebugFrameColumn:
    try:
        stages = _image_processing_stages(image, parameters)
    except ValueError as exc:
        error = f"UID {acquisition.uid!r}, frame {event_index}: {exc}"
        return _DebugFrameColumn(
            acquisition=acquisition,
            suggestion_index=suggestion_index,
            event_index=event_index,
            stages=None,
            error=error,
        )
    return _DebugFrameColumn(
        acquisition=acquisition,
        suggestion_index=suggestion_index,
        event_index=event_index,
        stages=stages,
    )


def _debug_metadata_value(
    acquisition: _EnergyAlignmentDebugAcquisition,
    *path: str,
) -> Any:
    for start in (acquisition.start, acquisition.source_start):
        value: Any = start
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                break
            value = value[key]
        else:
            if value is not None:
                return _py(value)
    return None


def _format_debug_value(value: Any) -> str:
    value = _py(value)
    if isinstance(value, Real) and not isinstance(value, bool):
        return f"{float(value):.6g}"
    return str(value)


def _shortest_unique_uid_prefixes(uids: Sequence[str]) -> dict[str, str]:
    unique_uids = tuple(dict.fromkeys(uids))
    prefixes = {}
    for uid in unique_uids:
        length = min(8, len(uid))
        while length < len(uid) and any(
            other != uid and other.startswith(uid[:length])
            for other in unique_uids
        ):
            length += 1
        prefixes[uid] = uid[:length]
    return prefixes


def _debug_column_label(
    column: _DebugFrameColumn,
    uid_prefix: str,
) -> str:
    acquisition = column.acquisition
    lines = []
    scan_id = _debug_metadata_value(acquisition, "scan_id")
    if scan_id is not None:
        lines.append(f"scan_id={_format_debug_value(scan_id)}")
    requested_energy = _debug_metadata_value(
        acquisition, "BMM_agent", "requested_energy"
    )
    if requested_energy is not None:
        lines.append(f"requested_energy={_format_debug_value(requested_energy)}")
    beamline_energy = _debug_metadata_value(acquisition, "Beamline", "energy")
    if beamline_energy is not None:
        lines.append(f"Beamline.energy={_format_debug_value(beamline_energy)}")
    lines.append(f"UID={uid_prefix}")

    suggestion = acquisition.suggestions[column.suggestion_index]
    if suggestion:
        if "_id" in suggestion:
            lines.append(f"suggestion _id={_format_debug_value(suggestion['_id'])}")
        for name, value in suggestion.items():
            if name != "_id":
                lines.append(f"{name}={_format_debug_value(value)}")
    else:
        lines.append("suggestion unnamed")

    energy = acquisition.energies[column.event_index]
    if energy is not None:
        lines.append(f"{acquisition.energy_field}={energy:.6g}")
    lines.append(
        f"{acquisition.intensity_field}="
        f"{acquisition.intensities[column.event_index]:.6g}"
    )
    return "\n".join(lines)


def _debug_reference_uids(
    acquisitions: Sequence[_EnergyAlignmentDebugAcquisition],
) -> tuple[str, ...]:
    references = []
    seen = set()
    for acquisition in acquisitions:
        for start in (acquisition.start, acquisition.source_start):
            value: Any = start
            for key in ("BMM_agent", "reference_scan_uid"):
                if not isinstance(value, Mapping) or key not in value:
                    break
                value = value[key]
            else:
                for reference in _to_list(value):
                    reference = _py(reference)
                    if (
                        isinstance(reference, str)
                        and reference
                        and reference not in seen
                    ):
                        seen.add(reference)
                        references.append(reference)
    return tuple(references)


def _debug_figure_title(
    mode: str,
    acquisitions: Sequence[_EnergyAlignmentDebugAcquisition],
    *,
    profile: EnergyAlignmentProfile,
    pixel_size_um: tuple[float, float],
) -> str:
    acquisition_uids = tuple(dict.fromkeys(item.uid for item in acquisitions))
    reference_uids = _debug_reference_uids(acquisitions)
    references = ", ".join(reference_uids) if reference_uids else "none"
    return (
        f"Energy alignment debug — {mode}\n"
        f"profile={profile.name} | acquisition UIDs={', '.join(acquisition_uids)} | "
        f"reference UIDs={references} | pixel calibration: "
        f"x={pixel_size_um[0]:.6g} µm/px, y={pixel_size_um[1]:.6g} µm/px"
    )


def _collect_energy_alignment_debug_columns(
    acquisitions: Sequence[_EnergyAlignmentDebugAcquisition],
    parameters: BeamEvaluationConfig,
) -> list[_DebugFrameColumn]:
    columns = []
    for acquisition in acquisitions:
        frames = _read_energy_alignment_debug_frames(acquisition)
        for suggestion_index, image in enumerate(frames):
            columns.append(
                _process_energy_alignment_debug_frame(
                    acquisition,
                    image,
                    suggestion_index=suggestion_index,
                    event_index=suggestion_index,
                    parameters=parameters,
                )
            )
    return columns


def _debug_overlay_label(column: _DebugFrameColumn, uid_prefix: str) -> str:
    acquisition = column.acquisition
    energy = acquisition.energies[column.event_index]
    if energy is not None:
        energy_label = f"{acquisition.energy_field}={energy:.6g}"
    elif (
        beamline_energy := _debug_metadata_value(
            acquisition, "Beamline", "energy"
        )
    ) is not None:
        energy_label = f"Beamline.energy={_format_debug_value(beamline_energy)}"
    elif (
        requested_energy := _debug_metadata_value(
            acquisition, "BMM_agent", "requested_energy"
        )
    ) is not None:
        energy_label = f"requested_energy={_format_debug_value(requested_energy)}"
    else:
        energy_label = "energy unavailable"

    suggestion = acquisition.suggestions[column.suggestion_index]
    suggestion_id = (
        f", _id={_format_debug_value(suggestion['_id'])}"
        if "_id" in suggestion
        else ""
    )
    return f"{energy_label} | UID={uid_prefix}{suggestion_id}"


def _render_energy_alignment_debug_grid(
    acquisitions: Sequence[_EnergyAlignmentDebugAcquisition],
    *,
    profile: EnergyAlignmentProfile,
    pixel_size_um: tuple[float, float],
    include_overlay: bool,
    mode: str,
    pyplot: Any,
) -> Figure:
    columns = _collect_energy_alignment_debug_columns(
        acquisitions, profile.evaluation
    )
    row_names = _configured_image_stage_names(profile.evaluation)
    column_count = len(columns) + int(include_overlay)
    figure = pyplot.figure(
        figsize=(max(8.0, 4.0 * column_count), max(6.0, 3.2 * len(row_names)))
    )
    grid = figure.add_gridspec(
        len(row_names),
        column_count,
        left=0.06,
        right=0.98,
        bottom=0.06,
        top=0.88,
        hspace=0.42,
        wspace=0.32,
    )
    uid_prefixes = _shortest_unique_uid_prefixes(
        [column.acquisition.uid for column in columns]
    )
    row_limits = [
        _debug_panel_limits(
            [
                column.stages[row_index]
                for column in columns
                if column.stages is not None
            ]
        )
        for row_index in range(len(row_names))
    ]

    for row_index, row_name in enumerate(row_names):
        for column_index, column in enumerate(columns):
            _render_energy_alignment_debug_panel(
                figure,
                grid[row_index, column_index],
                stage=(
                    column.stages[row_index]
                    if column.stages is not None
                    else None
                ),
                pixel_size_um=pixel_size_um,
                row_label=row_name if column_index == 0 else "",
                column_label=(
                    _debug_column_label(
                        column,
                        uid_prefixes[column.acquisition.uid],
                    )
                    if row_index == 0
                    else ""
                ),
                final_stage=row_index == len(row_names) - 1,
                limits=row_limits[row_index],
                error=column.error,
                pyplot=pyplot,
            )

        if include_overlay:
            overlay = tuple(
                (
                    _debug_overlay_label(
                        column,
                        uid_prefixes[column.acquisition.uid],
                    ),
                    column.stages[row_index],
                )
                for column in columns
                if column.stages is not None
            )
            overlay_error = None
            if not overlay:
                overlay_error = next(
                    (
                        column.error
                        for column in columns
                        if column.error is not None
                    ),
                    "No processable per-energy images",
                )
            source_count = len(
                {column.acquisition.source_uid for column in columns}
            )
            _render_energy_alignment_debug_panel(
                figure,
                grid[row_index, -1],
                stage=None,
                overlay=overlay,
                error=overlay_error,
                pixel_size_um=pixel_size_um,
                column_label=(
                    "all energies\n"
                    f"{len(columns)} frames from {source_count} per-energy runs"
                    if row_index == 0
                    else ""
                ),
                pyplot=pyplot,
            )

    figure.suptitle(
        _debug_figure_title(
            mode,
            acquisitions,
            profile=profile,
            pixel_size_um=pixel_size_um,
        )
    )
    return figure


def _render_per_energy_debug(
    acquisitions: Sequence[_EnergyAlignmentDebugAcquisition],
    *,
    profile: EnergyAlignmentProfile,
    pixel_size_um: tuple[float, float],
    pyplot: Any,
) -> Figure:
    return _render_energy_alignment_debug_grid(
        acquisitions,
        profile=profile,
        pixel_size_um=pixel_size_um,
        include_overlay=False,
        mode="per-energy",
        pyplot=pyplot,
    )


def _render_multi_energy_debug(
    acquisitions: Sequence[_EnergyAlignmentDebugAcquisition],
    *,
    profile: EnergyAlignmentProfile,
    pixel_size_um: tuple[float, float],
    pyplot: Any,
) -> Figure:
    return _render_energy_alignment_debug_grid(
        acquisitions,
        profile=profile,
        pixel_size_um=pixel_size_um,
        include_overlay=True,
        mode="multi-energy from per-energy runs",
        pyplot=pyplot,
    )


# --------- Dash live plotting callback ---------


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
        update_interval_ms: int = 500,
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
        self._metric_observations: dict[str, list[tuple[int, float]]] = {}
        self._pending_trial_indices: list[int] = []
        self._trial_index = -1

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
        self._metric_minimize = self._metric_minimize_map(opt_config)

    def _metric_minimize_map(self, opt_config) -> dict[str, bool]:
        """Return whether each metric should be minimized when computing best-so-far."""
        minimize_by_metric: dict[str, bool] = {}
        objective = opt_config.objective

        objectives = getattr(objective, "objectives", None)
        if objectives is not None:
            for obj in objectives:
                minimize_by_metric[obj.metric.name] = bool(obj.minimize)
        else:
            # presumably a single objective
            minimize_by_metric[objective.metric_names[0]] = bool(objective.minimize)
        if hasattr(objective, "metric"):
            minimize_by_metric[objective.metric.name] = bool(objective.minimize)


        for metric_name in opt_config.objective.metric_names:
            minimize_by_metric.setdefault(metric_name, False)
        return minimize_by_metric

    # -- Bluesky callback hooks ------------------------------------------------

    def start(self, doc: RunStart) -> None:
        """Bump the version so the app refreshes at the start of a run."""
        suggestions = doc.get("blop_suggestions", [])
        with self._lock:
            self._pending_trial_indices = [int(suggestion["_id"]) for suggestion in suggestions if "_id" in suggestion]
            self._version += 1

    def event(self, doc: Event) -> Event:
        """Record observed DOF and metric values and bump the version on each new trial."""
        data = doc.get("data", {})
        trial_indices = self._event_trial_indices(data)
        with self._lock:
            for name in self._dof_names:
                if name in data:
                    self._observed.setdefault(name, []).extend(_to_list(data[name]))
            for metric_name in self._objective_names:
                if metric_name in data:
                    values = _to_list(data[metric_name])
                    for trial_index, value in zip(trial_indices, values):
                        self._metric_observations.setdefault(metric_name, []).append((trial_index, float(value)))
            self._version += 1
        return doc

    def _event_trial_indices(self, data: dict[str, Any]) -> list[int]:
        max_len = 1
        for name in (*self._dof_names, *self._objective_names):
            if name in data:
                max_len = max(max_len, len(_to_list(data[name])))

        explicit_indices = None
        for key in ("trial_index", "trial_indices"):
            if key in data:
                explicit_indices = [int(v) for v in _to_list(data[key])]
                break
        if explicit_indices:
            indices = explicit_indices[:max_len]
        elif self._pending_trial_indices:
            indices = self._pending_trial_indices[:max_len]
        else:
            indices = list(range(self._trial_index + 1, self._trial_index + 1 + max_len))

        if len(indices) < max_len:
            start = indices[-1] + 1 if indices else self._trial_index + 1
            indices.extend(range(start, start + max_len - len(indices)))
        self._trial_index = max(self._trial_index, max(indices))
        return indices

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

    def _best_observed_data_from_ax(self, metric_name: str) -> tuple[list[tuple[int, float]], bool] | None:
        try:
            df = self._agent.ax_client._experiment.lookup_data().df
        except Exception:
            return None

        if df is None or df.empty or not {"metric_name", "mean", "trial_index"}.issubset(df.columns):
            return None

        metric_df = df[df["metric_name"] == metric_name]
        if metric_df.empty:
            return None

        minimize = self._metric_minimize.get(metric_name, False)
        observations = []
        for trial_index in sorted(metric_df["trial_index"].dropna().unique()):
            values = metric_df.loc[metric_df["trial_index"] == trial_index, "mean"].dropna()
            if values.empty:
                continue
            value = values.min() if minimize else values.max()
            observations.append((int(trial_index), float(value)))
        return observations, minimize

    def compute_best_observed_figure(self, metric_name: str) -> go.Figure:
        """Build a trial-index trace of observed values and best observed value so far."""
        import plotly.graph_objects as go

        ax_data = self._best_observed_data_from_ax(metric_name)
        if ax_data is None:
            with self._lock:
                observations = list(self._metric_observations.get(metric_name, []))
                minimize = self._metric_minimize.get(metric_name, False)
        else:
            observations, minimize = ax_data

        if not observations:
            return self._message_figure(f"No observations recorded for metric '{metric_name}' yet.")

        observations.sort(key=lambda item: item[0])
        trial_indices = [trial_index for trial_index, _value in observations]
        values = [value for _trial_index, value in observations]

        running_best = []
        best = None
        for value in values:
            if best is None:
                best = value
            elif minimize:
                best = min(best, value)
            else:
                best = max(best, value)
            running_best.append(best)

        direction = "minimum" if minimize else "maximum"
        figure = go.Figure()
        figure.add_trace(
            go.Scatter(
                x=trial_indices,
                y=values,
                mode="markers",
                name="observed value",
                marker={"color": "rgba(128, 90, 213, 0.75)", "size": 8},
                hovertemplate=f"trial: %{{x}}<br>{metric_name}: %{{y:.4g}}<extra>observed</extra>",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=trial_indices,
                y=running_best,
                mode="lines+markers",
                line={"color": "#1f77b4", "shape": "hv", "width": 2},
                marker={"size": 5},
                name=f"best observed {direction}",
                hovertemplate=f"trial: %{{x}}<br>best {metric_name}: %{{y:.4g}}<extra></extra>",
            )
        )
        figure.update_layout(
            title=f"Best observed '{metric_name}' by trial",
            xaxis_title="Trial index",
            yaxis_title=metric_name,
            margin={"l": 60, "r": 30, "t": 50, "b": 50},
            template="plotly_white",
            legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
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
                        html.Div(
                            [
                                html.Label("Best-observed metric"),
                                dcc.Dropdown(
                                    options=[{"label": n, "value": n} for n in self._objective_names],
                                    value=default_objective,
                                    id="best-observed-metric-dropdown",
                                    clearable=False,
                                ),
                            ],
                            style={"flex": "1", "padding": "0 8px"},
                        ),
                    ],
                    style={"display": "flex", "maxWidth": "1100px", "margin": "0 auto"},
                ),
                dcc.Graph(id="surrogate-graph", style={"height": "58vh"}),
                dcc.Graph(id="best-observed-graph", style={"height": "34vh"}),
                dcc.Interval(id="surrogate-interval", interval=self._update_interval_ms, n_intervals=0),
                dcc.Store(id="surrogate-rendered-version", data=-1),
            ]
        )

        @app.callback(
            Output("surrogate-graph", "figure"),
            Output("best-observed-graph", "figure"),
            Output("surrogate-rendered-version", "data"),
            Input("surrogate-x-dropdown", "value"),
            Input("surrogate-y-dropdown", "value"),
            Input("surrogate-objective-dropdown", "value"),
            Input("best-observed-metric-dropdown", "value"),
            Input("surrogate-interval", "n_intervals"),
            Input("surrogate-rendered-version", "data"),
        )
        def _update_graph(x_name, y_name, objective_name, best_metric_name, _n_intervals, rendered_version):
            from dash import ctx, no_update

            current_version = self.version
            # On a timer tick, only recompute when new data has arrived. Dropdown
            # changes always force a recompute.
            triggered_by_timer = ctx.triggered_id == "surrogate-interval"
            if triggered_by_timer and current_version == rendered_version:
                return no_update, no_update, no_update

            figure = self.compute_figure(x_name, y_name, objective_name)
            best_observed_figure = self.compute_best_observed_figure(best_metric_name)
            return figure, best_observed_figure, current_version

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
