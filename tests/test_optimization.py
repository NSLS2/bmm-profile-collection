from dataclasses import replace
import pickle
from types import SimpleNamespace
from unittest.mock import patch

from blop.ax import Objective, RangeDOF
from blop.plans import default_acquire
from bluesky import RunEngine
from bluesky.plan_stubs import null
from matplotlib import pyplot as plt
import numpy as np
from ophyd.sim import SynAxis, SynSignal
import pytest

import BMM.optimization as optimization_module
from BMM.optimization import (
    ENERGY_ALIGNMENT_PROFILES,
    PER_ENERGY_ALIGNMENT,
    BeamEvaluationConfig,
    EnergyAlignmentResources,
    ImageEvaluation,
    SurrogateModelDashCallback,
    _optimization_metadata,
    _full_width_half_maximum,
    _compute_processed_image_stats,
    _image_processing_stages,
    _preprocess_image,
    _write_energy_map,
    acquire_target_position,
    compute_image_stats,
    compute_multi_energy_alignment_metrics,
    compute_multi_energy_alignment_metrics_from_catalog,
    compute_stats,
    get_energy_alignment_profile,
    make_energy_alignment_agent,
    search_for_optimal_positions,
    show_energy_alignment_debug,
)


class Field:
    def __init__(self, value):
        self.value = value
        self.read_count = 0

    def read(self):
        self.read_count += 1
        return self.value


class Run(dict):
    metadata: dict


def run_with_fields(*, metadata=None, **fields):
    run = Run(
        {"primary": {"data": {name: Field(value) for name, value in fields.items()}}}
    )
    run.metadata = {} if metadata is None else metadata
    return run


def gaussian_image(
    center_x=18.0,
    center_y=12.0,
    sigma_x=3.0,
    sigma_y=1.5,
):
    y, x = np.indices((31, 41))
    return 100 * np.exp(
        -0.5
        * (
            ((x - center_x) / sigma_x) ** 2
            + ((y - center_y) / sigma_y) ** 2
        )
    )


@pytest.fixture
def make_profile_and_resources():
    def factory(
        *,
        catalog=None,
        change_edge_plan=None,
        read_energy=None,
    ):
        parameters = BeamEvaluationConfig(
            image_field="image",
            intensity_field="i0",
            x_crop=(4, 37),
        )
        profile = replace(
            PER_ENERGY_ALIGNMENT,
            name="test",
            sensors=("camera",),
            dofs=(
                RangeDOF(
                    actuator="motor",
                    bounds=(-1, 1),
                    parameter_type="float",
                    step_size=0.1,
                ),
            ),
            evaluation=parameters,
            optimization=replace(PER_ENERGY_ALIGNMENT.optimization, iterations=2),
        )
        resources = EnergyAlignmentResources(
            catalog=(
                {"reference": run_with_fields(image=gaussian_image())}
                if catalog is None
                else catalog
            ),
            actuators={"motor": SynAxis(name="motor")},
            sensors={"camera": SynSignal(name="camera", func=lambda: 1)},
            change_edge_plan=(
                (lambda *args, **kwargs: null())
                if change_edge_plan is None
                else change_edge_plan
            ),
            prompt_state=SimpleNamespace(prompt=True),
            read_energy=read_energy,
        )
        return profile, resources

    return factory


def test_full_width_half_maximum_interpolates_crossings():
    coordinates = np.array([10.0, 10.25, 10.5, 10.75, 11.0])
    profile = np.array([0.0, 1.0, 2.0, 1.0, 0.0])

    assert _full_width_half_maximum(profile, coordinates, axis="x") == 0.5

    for invalid_profile in (
        np.array([2.0, 1.0, 0.0, 0.0, 0.0]),
        np.zeros(5),
    ):
        with pytest.raises(
            ValueError,
            match="Cannot compute x FWHM: peak is not bracketed at half maximum",
        ):
            _full_width_half_maximum(invalid_profile, coordinates, axis="x")


def test_image_processing_stages_match_evaluation_pipeline():
    parameters = BeamEvaluationConfig(
        image_field="image",
        intensity_field="i0",
        x_crop=(8, 29),
        y_crop=(4, 21),
        upscale_factor=4,
    )
    image = gaussian_image()
    image[12, 7] = 10_000
    image[3, 18] = 20_000

    stats = compute_image_stats(image, parameters)

    assert stats.centroid_x == pytest.approx(18, abs=0.05)
    assert stats.centroid_y == pytest.approx(12, abs=0.05)
    assert np.isfinite([stats.fwhm_x, stats.fwhm_y]).all()
    assert stats.fwhm_x > stats.fwhm_y > 0

    changed_distractors = image.copy()
    changed_distractors[12, 7] = 30_000
    changed_distractors[3, 18] = 40_000
    assert compute_image_stats(changed_distractors, parameters) == stats

    scale_two_stats = compute_image_stats(
        image,
        replace(parameters, upscale_factor=2),
    )
    for metric in ("fwhm_x", "fwhm_y", "centroid_x", "centroid_y"):
        assert getattr(scale_two_stats, metric) == pytest.approx(
            getattr(stats, metric), abs=0.25
        )

    stages = _image_processing_stages(image, parameters)
    processed = _preprocess_image(image, parameters)
    assert [stage.name for stage in stages] == [
        "grayscale",
        "crop",
        "Gaussian σ=2.0",
        "Otsu threshold",
        "resize ×4",
    ]
    assert stages[-2].threshold is not None
    assert np.array_equal(processed.image, stages[-1].image)
    assert np.array_equal(processed.x_coordinates, stages[-1].x_coordinates)
    assert np.array_equal(processed.y_coordinates, stages[-1].y_coordinates)
    assert _compute_processed_image_stats(
        processed.image,
        processed.x_coordinates,
        processed.y_coordinates,
    ) == stats
    assert processed.image.shape == (68, 84)
    assert np.diff(processed.x_coordinates) == pytest.approx(0.25)
    assert np.diff(processed.y_coordinates) == pytest.approx(0.25)
    assert processed.x_coordinates.mean() == pytest.approx(18)
    assert processed.y_coordinates.mean() == pytest.approx(12)


def test_preprocess_image_allows_each_step_to_be_disabled():
    image = np.zeros((7, 9))
    image[3, 4] = 10
    parameters = BeamEvaluationConfig(
        image_field="image",
        intensity_field="i0",
        x_crop=None,
        y_crop=None,
        blur_sigma=None,
        upscale_factor=None,
    )

    stages = _image_processing_stages(image, parameters)
    processed = _preprocess_image(image, parameters)
    stats = compute_image_stats(image, parameters)

    assert [stage.name for stage in stages] == ["grayscale", "Otsu threshold"]
    assert processed.name == "Otsu threshold"
    assert processed.image.shape == image.shape
    assert np.array_equal(processed.x_coordinates, np.arange(image.shape[1]))
    assert np.array_equal(processed.y_coordinates, np.arange(image.shape[0]))
    assert np.count_nonzero(processed.image) == 1
    assert processed.image[3, 4] == 10
    assert stats.centroid_x == 4.0
    assert stats.centroid_y == 3.0
    assert stats.fwhm_x == 1.0
    assert stats.fwhm_y == 1.0


@pytest.mark.parametrize(
    ("image", "parameters", "message"),
    [
        pytest.param(
            np.ones((2, 31, 41)),
            BeamEvaluationConfig("image", "i0", (8, 29), (4, 21)),
            "ambiguous 3-D shape",
            id="frame-stack",
        ),
        pytest.param(
            gaussian_image(),
            BeamEvaluationConfig("image", "i0", (8, 8), (4, 21)),
            "Invalid x crop",
            id="empty-x-crop",
        ),
        pytest.param(
            gaussian_image(),
            BeamEvaluationConfig("image", "i0", (8, 42), (4, 21)),
            "Invalid x crop",
            id="out-of-bounds-x-crop",
        ),
        pytest.param(
            gaussian_image(),
            BeamEvaluationConfig("image", "i0", (8, 29), (-1, 21)),
            "Invalid y crop",
            id="negative-y-crop",
        ),
        pytest.param(
            gaussian_image(),
            BeamEvaluationConfig("image", "i0", (8, 29), (4, 32)),
            "Invalid y crop",
            id="out-of-bounds-y-crop",
        ),
        pytest.param(
            np.full((31, 41), np.nan),
            BeamEvaluationConfig("image", "i0", (8, 29), (4, 21)),
            "Image contains non-finite or negative pixels",
            id="non-finite-pixels",
        ),
        pytest.param(
            -gaussian_image(),
            BeamEvaluationConfig("image", "i0", (8, 29), (4, 21)),
            "Image contains non-finite or negative pixels",
            id="negative-pixels",
        ),
        pytest.param(
            np.ones((31, 41)),
            BeamEvaluationConfig("image", "i0", (8, 29), (4, 21)),
            "Image has no positive signal after Otsu thresholding",
            id="constant-image",
        ),
        pytest.param(
            gaussian_image(center_x=8),
            BeamEvaluationConfig("image", "i0", (8, 29), (4, 21)),
            "Cannot compute x FWHM: peak is not bracketed at half maximum",
            id="beam-at-crop-boundary",
        ),
    ],
)
def test_compute_image_stats_rejects_invalid_input(image, parameters, message):
    with pytest.raises(ValueError, match=message):
        compute_image_stats(image, parameters)


def test_multi_energy_alignment_metrics_compute_horizontal_stability_in_pixels():
    parameters = BeamEvaluationConfig(
        "image",
        "i0",
        blur_sigma=None,
        upscale_factor=None,
    )
    reference = gaussian_image(center_x=18.0, sigma_x=3.0)
    images = (
        gaussian_image(center_x=17.0, sigma_x=2.0),
        gaussian_image(center_x=19.0, sigma_x=4.0),
    )
    intensities = np.array([7.0, 11.0])

    metrics = compute_multi_energy_alignment_metrics(
        reference,
        images,
        intensities,
        parameters,
    )

    assert set(metrics) == {
        "centroid_x_offset_mean_px",
        "centroid_x_std_px",
        "centroid_x_span_px",
        "centroid_x_rmse_px",
        "fwhm_x_mean_px",
        "fwhm_x_std_px",
        "fwhm_x_rms_px",
        "fwhm_x_rms_normalized",
        "intensity_min",
        "intensity_mean",
    }
    assert all(type(value) is float for value in metrics.values())
    assert metrics["centroid_x_offset_mean_px"] == pytest.approx(0.0, abs=1e-12)
    assert metrics["centroid_x_std_px"] == pytest.approx(1.0)
    assert metrics["centroid_x_span_px"] == pytest.approx(2.0)
    assert metrics["centroid_x_rmse_px"] == pytest.approx(1.0)

    expected_fwhm_x_px = np.array(
        [compute_image_stats(image, parameters).fwhm_x for image in images]
    )
    reference_fwhm_x_px = compute_image_stats(reference, parameters).fwhm_x
    assert metrics["fwhm_x_mean_px"] == pytest.approx(
        float(np.mean(expected_fwhm_x_px))
    )
    assert metrics["fwhm_x_std_px"] == pytest.approx(
        float(np.std(expected_fwhm_x_px, ddof=0))
    )
    assert metrics["fwhm_x_rms_px"] == pytest.approx(
        float(np.sqrt(np.mean(expected_fwhm_x_px**2)))
    )
    assert metrics["fwhm_x_rms_normalized"] == pytest.approx(
        metrics["fwhm_x_rms_px"] / reference_fwhm_x_px
    )
    assert metrics["intensity_min"] == 7.0
    assert metrics["intensity_mean"] == 9.0


def test_multi_energy_alignment_metrics_from_catalog_reads_existing_runs():
    parameters = BeamEvaluationConfig(
        "image",
        "i0",
        blur_sigma=None,
        upscale_factor=None,
    )
    catalog = {
        "reference": run_with_fields(image=gaussian_image(center_x=18.0)),
        "low": run_with_fields(
            image=gaussian_image(center_x=17.0),
            i0=np.array([4.0]),
        ),
        "high": run_with_fields(
            image=gaussian_image(center_x=19.0),
            i0=np.array([8.0]),
        ),
    }

    metrics = compute_multi_energy_alignment_metrics_from_catalog(
        catalog=catalog,
        reference_uid="reference",
        per_energy_uids=("low", "high"),
        parameters=parameters,
    )

    assert metrics["centroid_x_span_px"] == pytest.approx(2.0)
    assert metrics["intensity_mean"] == 6.0
    assert catalog["reference"]["primary"]["data"]["image"].read_count == 1
    assert catalog["low"]["primary"]["data"]["image"].read_count == 1
    assert catalog["low"]["primary"]["data"]["i0"].read_count == 1
    assert catalog["high"]["primary"]["data"]["image"].read_count == 1
    assert catalog["high"]["primary"]["data"]["i0"].read_count == 1


@pytest.mark.parametrize(
    "outcome",
    [
        "fwhm_x",
        "fwhm_y",
        "centroid_x",
        "centroid_y",
        "centroid_distance",
        "intensity",
    ],
)
def test_profile_accepts_image_evaluation_outcomes(outcome):
    profile = replace(
        PER_ENERGY_ALIGNMENT,
        name=f"{outcome}-objective",
        objectives=(Objective(name=outcome, minimize=True),),
    )

    assert get_energy_alignment_profile(profile) is profile


def test_profile_rejects_unsupported_evaluation_outcome():
    with pytest.raises(ValueError, match="beam_width"):
        replace(
            PER_ENERGY_ALIGNMENT,
            name="unsupported-outcome",
            objectives=(Objective(name="beam_width", minimize=True),),
        )


def make_image_evaluator():
    parameters = BeamEvaluationConfig(
        image_field="image",
        intensity_field="i0",
        x_crop=(4, 37),
    )
    catalog = {"reference": run_with_fields(image=gaussian_image())}
    return ImageEvaluation(catalog, "reference", parameters), catalog


def test_image_evaluation_pairs_acquisition_metadata_with_suggestions():
    evaluator, catalog = make_image_evaluator()
    first_image = gaussian_image(center_y=14, sigma_y=2)
    second_image = gaussian_image(center_x=20, sigma_x=4)
    acquired_images = np.stack((second_image, first_image))
    acquired_intensities = np.array([2_500_000.0, 1_250_000.0])
    catalog["acquired"] = run_with_fields(
        metadata={
            "start": {
                "blop_suggestions": [{"_id": "second"}, {"_id": "first"}]
            }
        },
        image=acquired_images,
        i0=acquired_intensities,
    )

    outcomes = evaluator(
        "acquired",
        [{"_id": "first"}, {"_id": "second"}],
    )

    assert [outcome["_id"] for outcome in outcomes] == ["first", "second"]
    assert [outcome["intensity"] for outcome in outcomes] == [
        1_250_000.0,
        2_500_000.0,
    ]
    metric_names = {
        "fwhm_x",
        "fwhm_y",
        "centroid_x",
        "centroid_y",
        "centroid_distance",
        "intensity",
    }
    assert set(outcomes[0]) == {"_id", *metric_names}
    assert all(
        type(outcome[metric]) is float
        for outcome in outcomes
        for metric in metric_names
    )
    assert outcomes[0]["centroid_distance"] == pytest.approx(2, abs=0.05)
    assert outcomes[1]["centroid_distance"] == pytest.approx(2, abs=0.05)
    assert outcomes[0]["fwhm_y"] > outcomes[1]["fwhm_y"]
    assert outcomes[1]["fwhm_x"] > outcomes[0]["fwhm_x"]
    assert not np.allclose(
        acquired_images.sum(axis=(1, 2)),
        acquired_intensities,
    )
    assert catalog["reference"]["primary"]["data"]["image"].read_count == 1
    assert catalog["acquired"]["primary"]["data"]["image"].read_count == 1
    assert catalog["acquired"]["primary"]["data"]["i0"].read_count == 1


def test_image_evaluation_skips_catalog_when_there_are_no_suggestions():
    evaluator, _ = make_image_evaluator()

    class UnreadableCatalog:
        def __getitem__(self, key):
            raise AssertionError(f"Unexpected catalog read for {key!r}")

    evaluator.tiled_client = UnreadableCatalog()

    assert evaluator("unused", []) == []


@pytest.mark.parametrize(
    "intensity_data",
    [1_250_000.0, np.array([1_250_000.0])],
    ids=["scalar", "singleton"],
)
def test_image_evaluation_accepts_one_scalar_ion_reading(intensity_data):
    evaluator, catalog = make_image_evaluator()
    catalog["acquired"] = run_with_fields(
        image=gaussian_image(center_x=19)[np.newaxis, ...],
        i0=intensity_data,
    )

    outcomes = evaluator("acquired", [{"_id": "only"}])

    assert outcomes[0]["intensity"] == 1_250_000.0


@pytest.mark.parametrize(
    ("images", "intensities"),
    [
        (
            np.stack((gaussian_image(),)),
            np.array([1_250_000.0, 2_500_000.0]),
        ),
        (
            np.stack((gaussian_image(), gaussian_image(center_x=20))),
            np.array([1_250_000.0]),
        ),
    ],
    ids=["image-count", "ion-count"],
)
def test_image_evaluation_rejects_mismatched_payload_counts(images, intensities):
    evaluator, catalog = make_image_evaluator()
    catalog["acquired"] = run_with_fields(
        metadata={
            "start": {
                "blop_suggestions": [{"_id": "first"}, {"_id": "second"}]
            }
        },
        image=images,
        i0=intensities,
    )

    with pytest.raises(ValueError):
        evaluator("acquired", [{"_id": "first"}, {"_id": "second"}])


def test_compute_stats_reports_catalog_ion_reading(
    make_profile_and_resources,
    capsys,
):
    run = run_with_fields(image=gaussian_image(), i0=np.array([1_250_000.0]))
    profile, resources = make_profile_and_resources(catalog={"acquired": run})

    stats = compute_stats("acquired", profile=profile, resources=resources)

    output = capsys.readouterr().out
    assert stats.centroid_x == pytest.approx(18, abs=0.05)
    for metric in ("fwhm_x", "fwhm_y", "centroid_x", "centroid_y"):
        assert f"{metric}=" in output
    assert "intensity=1250000.0" in output
    assert run["primary"]["data"]["image"].read_count == 1
    assert run["primary"]["data"]["i0"].read_count == 1


def test_energy_alignment_debug_expands_outer_run_and_renders_per_energy_grid(
    make_profile_and_resources,
):
    plt.switch_backend("Agg")
    first_uid = "aaaaaaaa111111111111111111111111"
    second_uid = "aaaaaaaa222222222222222222222222"
    first_images = np.stack(
        (gaussian_image(center_x=17), 2 * gaussian_image(center_x=19))
    )
    first = run_with_fields(
        metadata={
            "start": {
                "uid": first_uid,
                "scan_id": 41,
                "blop_suggestions": [
                    {"_id": "first-a", "motor": -0.2},
                    {"_id": "first-b", "motor": 0.3},
                ],
            }
        },
        image=first_images,
        i0=np.array([100.0, 200.0]),
        dcm_energy=np.array([7112.0, 7113.0]),
    )
    second = run_with_fields(
        metadata={
            "start": {
                "uid": second_uid,
                "scan_id": 42,
                "blop_suggestions": [{"_id": "second-a", "motor": 0.1}],
            }
        },
        image=gaussian_image(center_y=13),
        i0=300.0,
        dcm_energy=7114.0,
    )
    outer = run_with_fields(
        metadata={
            "start": {
                "uid": "outer-optimization-full-uid",
                "BMM_agent": {
                    "requested_energy": "Fe",
                    "reference_scan_uid": "reference-full-uid",
                },
                "Beamline": {"energy": 7112.0},
            }
        },
        acquisition_uid=np.array([first_uid, second_uid, first_uid]),
    )
    profile, resources = make_profile_and_resources(
        catalog={first_uid: first, second_uid: second, "outer": outer}
    )

    figure = show_energy_alignment_debug(
        "outer",
        pixel_size_um=(2.0, 3.0),
        profile=profile,
        resources=resources,
    )
    try:
        figure.canvas.draw()
        row_names = [
            stage.name
            for stage in _image_processing_stages(
                first_images[0], profile.evaluation
            )
        ]
        image_axes = [
            axis
            for axis in figure.axes
            if axis.get_gid() == "energy-alignment-image"
        ]
        x_axes = [
            axis
            for axis in figure.axes
            if axis.get_gid() == "energy-alignment-x-marginal"
        ]
        y_axes = [
            axis
            for axis in figure.axes
            if axis.get_gid() == "energy-alignment-y-marginal"
        ]
        assert (
            len(image_axes)
            == len(x_axes)
            == len(y_axes)
            == len(row_names) * 3
        )
        assert [
            image_axes[index * 3].get_ylabel().splitlines()[0]
            for index in range(len(row_names))
        ] == row_names

        displayed = np.asarray(image_axes[0].images[0].get_array())
        assert x_axes[0].lines[0].get_ydata() == pytest.approx(
            displayed.sum(axis=0)
        )
        assert y_axes[0].lines[0].get_xdata() == pytest.approx(
            displayed.sum(axis=1)
        )
        assert x_axes[0].lines[0].get_xdata() == pytest.approx(
            np.arange(first_images.shape[2]) * 2.0
        )
        assert y_axes[0].lines[0].get_ydata() == pytest.approx(
            np.arange(first_images.shape[1]) * 3.0
        )
        assert len({axis.images[0].get_clim() for axis in image_axes[:3]}) == 1

        final_text = "\n".join(
            text.get_text() for text in image_axes[-3].texts
        )
        stats = compute_image_stats(first_images[0], profile.evaluation)
        assert f"{stats.fwhm_x * 2.0:.3g} µm" in final_text
        assert f"{stats.fwhm_y * 3.0:.3g} µm" in final_text
        assert "centroid x" in final_text and "px" in final_text
        assert any(
            "threshold =" in text.get_text()
            for axis in image_axes
            for text in axis.texts
        )

        column_context = "\n".join(axis.get_title() for axis in x_axes)
        assert "scan_id=41" in column_context
        assert "requested_energy=Fe" in column_context
        assert "Beamline.energy=7112" in column_context
        assert "UID=aaaaaaaa1" in column_context
        assert "UID=aaaaaaaa2" in column_context
        assert "suggestion _id=first-a" in column_context
        assert "motor=-0.2" in column_context
        assert "dcm_energy=7114" in column_context
        assert "i0=300" in column_context

        title = figure._suptitle.get_text()
        assert "per-energy" in title and f"profile={profile.name}" in title
        assert first_uid in title and second_uid in title
        assert "reference-full-uid" in title
        assert "x=2 µm/px, y=3 µm/px" in title
        assert outer["primary"]["data"]["acquisition_uid"].read_count == 1
        assert first["primary"]["data"]["image"].read_count == 1
        assert second["primary"]["data"]["image"].read_count == 1
    finally:
        plt.close(figure)


def test_energy_alignment_debug_overlays_multiple_per_energy_runs(
    make_profile_and_resources,
):
    plt.switch_backend("Agg")
    energies = (7000.0, 7100.0, 7200.0)
    acquisition_uids = tuple(
        f"acquisition-{index:02d}-full-uid" for index in range(len(energies))
    )
    outer_uids = tuple(
        f"optimization-{index:02d}-full-uid" for index in range(len(energies))
    )
    catalog = {}
    acquisitions = []
    outers = []
    for index, (energy, acquisition_uid, outer_uid) in enumerate(
        zip(energies, acquisition_uids, outer_uids, strict=True)
    ):
        acquisition = run_with_fields(
            metadata={
                "start": {
                    "uid": acquisition_uid,
                    "scan_id": 50 + index,
                    "blop_suggestions": [
                        {"_id": f"energy-{index}", "motor": index / 10}
                    ],
                }
            },
            image=(index + 1) * gaussian_image(center_x=16 + index),
            i0=10.0 + index,
        )
        outer = run_with_fields(
            metadata={
                "start": {
                    "uid": outer_uid,
                    "BMM_agent": {
                        "requested_energy": f"edge-{index}",
                        "reference_scan_uid": "reference-full-uid",
                    },
                    "Beamline": {"energy": energy},
                }
            },
            acquisition_uid=np.array([acquisition_uid]),
        )
        catalog[acquisition_uid] = acquisition
        catalog[outer_uid] = outer
        acquisitions.append(acquisition)
        outers.append(outer)

    profile, resources = make_profile_and_resources(catalog=catalog)
    figure = show_energy_alignment_debug(
        outer_uids,
        pixel_size_um=(2.0, 3.0),
        profile=profile,
        resources=resources,
    )
    try:
        figure.canvas.draw()
        image_axes = [
            axis
            for axis in figure.axes
            if axis.get_gid() == "energy-alignment-image"
        ]
        x_axes = [
            axis
            for axis in figure.axes
            if axis.get_gid() == "energy-alignment-x-marginal"
        ]
        y_axes = [
            axis
            for axis in figure.axes
            if axis.get_gid() == "energy-alignment-y-marginal"
        ]
        row_count = len(
            _image_processing_stages(gaussian_image(), profile.evaluation)
        )
        assert len(image_axes) == len(x_axes) == len(y_axes) == row_count * 4

        overlay_index = next(
            index
            for index, axis in enumerate(x_axes)
            if axis.get_title().startswith("all energies")
        )
        overlay_image = image_axes[overlay_index]
        overlay_x = x_axes[overlay_index]
        overlay_y = y_axes[overlay_index]
        assert len(overlay_image.collections) == 3
        assert all(
            tuple(contours.levels) == (0.25, 0.5, 0.75)
            for contours in overlay_image.collections
        )
        assert len(overlay_x.lines) == len(overlay_y.lines) == 3
        assert all(np.max(line.get_ydata()) == 1.0 for line in overlay_x.lines)
        assert all(np.max(line.get_xdata()) == 1.0 for line in overlay_y.lines)
        assert overlay_image.get_legend() is not None
        assert all(axis.images for axis in image_axes[:3])
        assert max(np.max(axis.images[0].get_array()) for axis in image_axes[:3]) > 1

        top_titles = [axis.get_title() for axis in x_axes[:4]]
        assert [
            f"Beamline.energy={energy:.6g}" in top_titles[index]
            for index, energy in enumerate(energies)
        ] == [True, True, True]
        assert top_titles[-1].startswith("all energies")
        assert "3 frames from 3 per-energy runs" in top_titles[-1]
        assert [
            f"Beamline.energy={energy:.6g}" in line.get_label()
            for line, energy in zip(overlay_x.lines, energies, strict=True)
        ] == [True, True, True]

        title = figure._suptitle.get_text()
        assert "multi-energy from per-energy runs" in title
        assert all(uid in title for uid in acquisition_uids)
        assert "reference-full-uid" in title
        assert all(
            run["primary"]["data"]["image"].read_count == 1
            for run in acquisitions
        )
        assert all(
            run["primary"]["data"]["acquisition_uid"].read_count == 1
            for run in outers
        )
    finally:
        plt.close(figure)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("empty-uids", "uids"),
        ("outer-without-links", "bad-outer.*acquisition_uid"),
        ("energy-count", "energy-bad.*dcm_energy.*per-energy debug"),
        ("intensity-count", "intensity-bad.*i0"),
        ("image-count", "image-bad.*image"),
        ("scan-shaped-without-energy", "missing-energy.*multiple per-energy"),
    ],
)
def test_energy_alignment_debug_rejects_invalid_inputs(
    case,
    message,
    make_profile_and_resources,
):
    image = gaussian_image()
    pixel_size_um = (2.0, 3.0)
    uids = "direct"
    catalog = {
        "direct": run_with_fields(
            metadata={"start": {"uid": "direct"}},
            image=image,
            i0=1.0,
            dcm_energy=7000.0,
        )
    }

    if case == "empty-uids":
        uids = []
    elif case == "outer-without-links":
        uids = "bad-outer"
        catalog = {
            "bad-outer": run_with_fields(
                metadata={"start": {"uid": "bad-outer"}},
                unrelated=np.array([1.0]),
            )
        }
    elif case == "energy-count":
        uids = "energy-bad"
        catalog = {
            "energy-bad": run_with_fields(
                metadata={
                    "start": {
                        "uid": "energy-bad",
                        "blop_suggestions": [{"_id": "a"}, {"_id": "b"}],
                    }
                },
                image=np.stack((image, image)),
                i0=np.ones(3),
                dcm_energy=np.arange(3, dtype=float),
            )
        }
    elif case == "intensity-count":
        uids = "intensity-bad"
        catalog = {
            "intensity-bad": run_with_fields(
                metadata={
                    "start": {
                        "uid": "intensity-bad",
                        "blop_suggestions": [{"_id": "a"}, {"_id": "b"}],
                    }
                },
                image=np.stack((image, image)),
                i0=np.ones(1),
                dcm_energy=np.arange(2, dtype=float),
            )
        }
    elif case == "image-count":
        uids = "image-bad"
        catalog = {
            "image-bad": run_with_fields(
                metadata={
                    "start": {
                        "uid": "image-bad",
                        "blop_suggestions": [{"_id": "a"}, {"_id": "b"}],
                    }
                },
                image=image,
                i0=np.ones(2),
                dcm_energy=np.arange(2, dtype=float),
            )
        }
    elif case == "scan-shaped-without-energy":
        uids = "missing-energy"
        catalog = {
            "missing-energy": run_with_fields(
                metadata={
                    "start": {
                        "uid": "missing-energy",
                        "blop_suggestions": [{"_id": "only"}],
                    }
                },
                image=np.stack((image, image)),
                i0=1.0,
            )
        }

    profile, resources = make_profile_and_resources(catalog=catalog)
    try:
        with pytest.raises(ValueError, match=message):
            show_energy_alignment_debug(
                uids,
                pixel_size_um=pixel_size_um,
                profile=profile,
                resources=resources,
            )
    finally:
        plt.close("all")


def test_agent_factory_returns_fresh_agents(make_profile_and_resources):
    profile, resources = make_profile_and_resources()

    first = make_energy_alignment_agent(
        "reference",
        profile=profile,
        resources=resources,
        subscribe_to_dash=False,
    )
    second = make_energy_alignment_agent(
        "reference",
        profile=profile,
        resources=resources,
        subscribe_to_dash=False,
    )

    assert first is not second
    assert profile.dofs[0].actuator == "motor"
    assert profile.dofs[0].step_size == 0.1
    assert first.acquisition_plan is None
    assert second.acquisition_plan is None


def test_acquire_target_position_records_supplied_readables():
    camera_value = np.array([[1.0, 2.0], [3.0, 4.0]])
    ion_chamber_value = 1_250_000.0
    motor_position = 0.375
    camera = SynSignal(name="camera", func=lambda: camera_value)
    ion_chamber = SynSignal(name="ion_chamber", func=lambda: ion_chamber_value)
    motor = SynAxis(name="motor", value=motor_position)
    documents = []
    run_engine = RunEngine({}, call_returns_result=True)
    run_engine.subscribe(lambda name, doc: documents.append((name, doc)))

    with patch.object(motor, "set", wraps=motor.set) as set_motor:
        result = run_engine(
            acquire_target_position([camera, ion_chamber, motor])
        )

    starts = [doc for name, doc in documents if name == "start"]
    assert len(starts) == 1
    [start] = starts
    assert start["plan_name"] == "acquire_target_position"
    assert result.plan_result == start["uid"]

    events = [doc for name, doc in documents if name == "event"]
    assert len(events) == 1
    [event] = events
    np.testing.assert_array_equal(event["data"]["camera"], camera_value)
    assert event["data"]["ion_chamber"] == ion_chamber_value
    assert event["data"]["motor"] == motor_position
    assert set_motor.call_count == 0
    assert len([doc for name, doc in documents if name == "stop"]) == 1




def test_metadata_uses_profile_and_live_resources(make_profile_and_resources):
    profile, resources = make_profile_and_resources(read_energy=lambda: 7112.5)

    metadata = _optimization_metadata(
        "Fe",
        "reference",
        profile=profile,
        resources=resources,
    )

    assert metadata["Beamline"]["energy"] == 7112.5
    agent_metadata = metadata["BMM_agent"]
    assert agent_metadata["profile"] == "test"
    assert agent_metadata["iterations"] == 2
    assert agent_metadata["dofs"][0]["name"] == "motor"
    assert agent_metadata["sensors"] == ["camera"]
    assert agent_metadata["objectives"] == ["centroid_distance"]


def test_dash_callback_builds_app(make_profile_and_resources):
    profile, resources = make_profile_and_resources()
    agent = make_energy_alignment_agent(
        "reference",
        profile=profile,
        resources=resources,
        subscribe_to_dash=False,
    )

    callback = SurrogateModelDashCallback(agent)
    callback.event({"data": {"motor": 0.25}})
    figure = callback.compute_figure("motor", "motor", "centroid_distance")
    app = callback.build_app()

    assert callback.dof_names == ["motor"]
    assert callback.objective_names == ["centroid_distance"]
    assert callback.version == 1
    assert "two different DOFs" in figure.layout.annotations[0].text
    assert app.layout is not None


def test_energy_map_write_is_atomic_and_pickle_compatible(tmp_path):
    energy_map = {"Fe": [(0, {"motor": 0.25}, {"intensity": (10.0, 0.0)})]}
    filename = tmp_path / "energy-map.pickle"

    _write_energy_map(filename, energy_map)

    with filename.open("rb") as stream:
        assert pickle.load(stream) == energy_map
    assert not (filename.parent / f".{filename.name}.tmp").exists()


def test_search_captures_and_reuses_target_reference(
    make_profile_and_resources,
    monkeypatch,
):
    class FakeAgent:
        def __init__(self, acquisition_plan):
            self.acquisition_plan = acquisition_plan

        def optimize(self, iterations):
            yield from self.acquisition_plan(
                [{"_id": "test", "motor": 0.0}],
                [resources.actuators["motor"]],
                [resources.sensors["camera"]],
            )

        def get_best_points(self):
            return [(0, {"motor": 0.0}, {"centroid_distance": (0.0, 0.0)})]

    reference_image = Field(gaussian_image())
    catalog = {"target": {"primary": {"data": {"image": reference_image}}}}
    lifecycle = []
    acquired_readables = []

    def acquire_target(readables):
        lifecycle.append("acquire")
        acquired_readables.append(tuple(readables))
        yield from null()
        return "target"

    def change_edge(energy, **kwargs):
        lifecycle.append(f"edge:{energy}")
        yield from null()

    profile, resources = make_profile_and_resources(
        catalog=catalog,
        change_edge_plan=change_edge,
    )
    evaluation_functions = []
    reference_scan_uids = []
    acquisition_plans = []

    def make_agent(
        reference_scan_uid,
        *,
        evaluation_function=None,
        acquisition_plan=None,
        **kwargs,
    ):
        reference_scan_uids.append(reference_scan_uid)
        evaluation_functions.append(evaluation_function)
        acquisition_plans.append(acquisition_plan)
        return FakeAgent(acquisition_plan)

    monkeypatch.setattr(
        optimization_module,
        "acquire_target_position",
        acquire_target,
    )
    monkeypatch.setattr(
        optimization_module,
        "make_energy_alignment_agent",
        make_agent,
    )
    documents = []
    run_engine = RunEngine({})
    run_engine.subscribe(lambda name, doc: documents.append((name, doc)))
    run_engine(
        search_for_optimal_positions(
            ["Fe", "Cu"],
            profile=profile,
            resources=resources,
        )
    )

    assert lifecycle == ["acquire", "edge:Fe", "edge:Cu"]
    assert acquired_readables == [
        (resources.sensors["camera"], resources.actuators["motor"])
    ]
    assert reference_scan_uids == ["target", "target"]
    assert [plan.func for plan in acquisition_plans] == [default_acquire] * 2
    acquisition_metadata = [plan.keywords["md"] for plan in acquisition_plans]
    assert [
        (
            metadata["BMM_agent"]["requested_energy"],
            metadata["BMM_agent"]["reference_scan_uid"],
        )
        for metadata in acquisition_metadata
    ] == [("Fe", "target"), ("Cu", "target")]
    starts = [doc for name, doc in documents if name == "start"]
    assert [
        (
            start["BMM_agent"]["requested_energy"],
            start["BMM_agent"]["reference_scan_uid"],
        )
        for start in starts
    ] == [("Fe", "target"), ("Cu", "target")]
    assert len(evaluation_functions) == 2
    assert evaluation_functions[0] is evaluation_functions[1]
    assert reference_image.read_count == 1


def test_search_uses_supplied_target_reference(
    make_profile_and_resources,
    monkeypatch,
):
    class FakeAgent:
        def optimize(self, iterations):
            yield from null()

        def get_best_points(self):
            return []

    def unexpected_target_acquisition(readables):
        raise AssertionError("search recaptured a supplied target")
        yield from null()

    profile, resources = make_profile_and_resources()
    reference_image = resources.catalog["reference"]["primary"]["data"]["image"]
    evaluation_functions = []
    reference_scan_uids = []
    acquisition_plans = []
    fake_agent = FakeAgent()

    def make_agent(
        reference_scan_uid,
        *,
        evaluation_function=None,
        acquisition_plan=None,
        **kwargs,
    ):
        reference_scan_uids.append(reference_scan_uid)
        evaluation_functions.append(evaluation_function)
        acquisition_plans.append(acquisition_plan)
        return fake_agent

    monkeypatch.setattr(
        optimization_module,
        "acquire_target_position",
        unexpected_target_acquisition,
    )
    monkeypatch.setattr(
        optimization_module,
        "make_energy_alignment_agent",
        make_agent,
    )
    RunEngine({})(
        search_for_optimal_positions(
            ["Fe", "Cu"],
            reference_scan_uid="reference",
            profile=profile,
            resources=resources,
        )
    )

    assert reference_scan_uids == ["reference", "reference"]
    assert [plan.func for plan in acquisition_plans] == [default_acquire] * 2
    assert [
        (
            plan.keywords["md"]["BMM_agent"]["requested_energy"],
            plan.keywords["md"]["BMM_agent"]["reference_scan_uid"],
        )
        for plan in acquisition_plans
    ] == [("Fe", "reference"), ("Cu", "reference")]
    assert len(evaluation_functions) == 2
    assert evaluation_functions[0] is evaluation_functions[1]
    assert reference_image.read_count == 1


def test_search_restores_prompt_after_failure(
    make_profile_and_resources,
    monkeypatch,
):
    def acquire_target(sensors):
        yield from null()
        return "target"

    def failing_change_edge(*args, **kwargs):
        yield from null()
        raise RuntimeError("energy change failed")

    profile, resources = make_profile_and_resources(
        catalog={"target": run_with_fields(image=gaussian_image())},
        change_edge_plan=failing_change_edge,
    )
    prompt_state = resources.prompt_state
    monkeypatch.setattr(
        optimization_module,
        "acquire_target_position",
        acquire_target,
    )

    with pytest.raises(RuntimeError, match="energy change failed"):
        RunEngine({})(
            search_for_optimal_positions(
                ["Fe"],
                profile=profile,
                resources=resources,
            )
        )

    assert prompt_state.prompt


def test_search_with_no_energies_skips_target_acquisition(
    make_profile_and_resources,
    monkeypatch,
):
    profile, resources = make_profile_and_resources()
    original_prompt = resources.prompt_state.prompt

    def unexpected_target_acquisition(sensors):
        raise AssertionError("empty search acquired a target")
        yield from null()

    monkeypatch.setattr(
        optimization_module,
        "acquire_target_position",
        unexpected_target_acquisition,
    )
    result = RunEngine({}, call_returns_result=True)(
        search_for_optimal_positions(
            [],
            profile=profile,
            resources=resources,
        )
    )

    assert result.plan_result == {}
    assert resources.prompt_state.prompt is original_prompt
