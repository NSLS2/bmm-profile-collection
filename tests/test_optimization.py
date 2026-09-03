from dataclasses import replace
import pickle
from types import SimpleNamespace
from unittest.mock import patch

from blop.plans import default_acquire
from bluesky import RunEngine
from bluesky.plan_stubs import deferred_pause, null
from bluesky.utils import RunEngineInterrupted
from matplotlib import pyplot as plt
import numpy as np
from ophyd.sim import SynAxis, SynSignal
import pytest

import BMM.optimization as optimization_module
from BMM.optimization import (
    ENERGY_ALIGNMENT_PROFILES,
    XAS_SI111_ALIGNMENT,
    AlignmentCostConfig,
    BeamEvaluationConfig,
    EnergyAlignmentResources,
    ImageEvaluation,
    OptimizationConfig,
    SurrogateModelDashCallback,
    UnusableBeamError,
    _optimization_metadata,
    _full_width_half_maximum,
    _compute_processed_image_stats,
    _image_processing_stages,
    _preprocess_image,
    _resolve_search_space,
    _write_agent_checkpoint,
    _write_energy_map,
    acquire_target_position,
    compute_alignment_cost,
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
        -0.5 * (((x - center_x) / sigma_x) ** 2 + ((y - center_y) / sigma_y) ** 2)
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
            XAS_SI111_ALIGNMENT,
            camera="camera",
            dof_bounds={
                "dcm_roll": (-1, 1),
                "m2_yaw": (-1, 1),
                "m2_lateral": (-1, 1),
            },
            search_half_widths=None,
            evaluation=parameters,
            optimization=replace(
                XAS_SI111_ALIGNMENT.optimization,
                iterations=2,
                initialization_budget=1,
                initialize_with_center=False,
            ),
        )
        resources = EnergyAlignmentResources(
            catalog=(
                {
                    "reference": run_with_fields(
                        image=gaussian_image(),
                        dcm_roll=0.25,
                        m2_yaw=0.0,
                        m2_lateral=0.0,
                    )
                }
                if catalog is None
                else catalog
            ),
            actuators={
                name: SynAxis(name=name)
                for name in ("dcm_roll", "m2_yaw", "m2_lateral")
            },
            sensors={
                "camera": SynSignal(name="camera", func=lambda: 1),
                "i0": SynSignal(name="i0", func=lambda: 1),
            },
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
    assert (
        _compute_processed_image_stats(
            processed.image,
            processed.x_coordinates,
            processed.y_coordinates,
        )
        == stats
    )
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


def test_xas_si111_profile_is_registered():
    assert ENERGY_ALIGNMENT_PROFILES == {"xas-si111": XAS_SI111_ALIGNMENT}
    assert get_energy_alignment_profile("xas-si111") is XAS_SI111_ALIGNMENT


@pytest.mark.parametrize(
    ("dof_bounds", "message"),
    [
        pytest.param(
            {"dcm_roll": (-1, 1), "m2_yaw": (-1, 1)},
            "must define bounds for exactly",
            id="missing",
        ),
        pytest.param(
            {
                "dcm_roll": (-1, 1),
                "m2_yaw": (-1, 1),
                "m2_lateral": (-1, 1),
                "extra": (-1, 1),
            },
            "must define bounds for exactly",
            id="extra",
        ),
        pytest.param(
            {
                "dcm_roll": (1, -1),
                "m2_yaw": (-1, 1),
                "m2_lateral": (-1, 1),
            },
            "invalid bounds for 'dcm_roll'",
            id="reversed",
        ),
    ],
)
def test_profile_rejects_invalid_dof_bounds(dof_bounds, message):
    with pytest.raises(ValueError, match=message):
        replace(XAS_SI111_ALIGNMENT, dof_bounds=dof_bounds)


@pytest.mark.parametrize(
    "minimum_intensity_fraction",
    [-0.1, np.inf, -np.inf, np.nan],
    ids=["negative", "positive-infinity", "negative-infinity", "nan"],
)
def test_profile_rejects_invalid_intensity_fraction(minimum_intensity_fraction):
    with pytest.raises(
        ValueError,
        match="Minimum intensity fraction must be finite and non-negative",
    ):
        replace(
            XAS_SI111_ALIGNMENT,
            minimum_intensity_fraction=minimum_intensity_fraction,
        )


def test_profile_rejects_change_edge_element_override():
    with pytest.raises(ValueError, match="change_edge_kwargs cannot contain 'el'"):
        replace(XAS_SI111_ALIGNMENT, change_edge_kwargs={"el": "Fe"})


def test_resources_require_selected_camera(make_profile_and_resources):
    profile, resources = make_profile_and_resources()
    profile = replace(profile, camera="missing-camera")

    with pytest.raises(ValueError, match="missing-camera"):
        make_energy_alignment_agent(
            "reference",
            profile=profile,
            resources=resources,
            subscribe_to_dash=False,
        )


def test_profile_rejects_unknown_search_half_width_dof():
    with pytest.raises(ValueError, match="unknown"):
        replace(XAS_SI111_ALIGNMENT, search_half_widths={"not_a_dof": 1.0})


def test_profile_rejects_non_positive_search_half_width():
    with pytest.raises(ValueError, match="must all be positive"):
        replace(XAS_SI111_ALIGNMENT, search_half_widths={"dcm_roll": 0.0})


def make_image_evaluator():
    parameters = BeamEvaluationConfig(
        image_field="image",
        intensity_field="i0",
        x_crop=(4, 37),
    )
    catalog = {"reference": run_with_fields(image=gaussian_image())}
    evaluator = ImageEvaluation(
        catalog,
        "reference",
        parameters,
        cost=AlignmentCostConfig(),
        nominal_dof_values={"motor": 0.0},
        dof_half_ranges={"motor": 1.0},
    )
    return evaluator, catalog


def test_image_evaluation_pairs_acquisition_metadata_with_suggestions():
    evaluator, catalog = make_image_evaluator()
    first_image = gaussian_image(center_y=14, sigma_y=2)
    second_image = gaussian_image(center_x=20, sigma_x=4)
    acquired_images = np.stack((second_image, first_image))
    acquired_intensities = np.array([2_500_000.0, 1_250_000.0])
    catalog["acquired"] = run_with_fields(
        metadata={"start": {"blop_suggestions": [{"_id": "second"}, {"_id": "first"}]}},
        image=acquired_images,
        i0=acquired_intensities,
    )

    outcomes = evaluator(
        "acquired",
        [{"_id": "first", "motor": 0.1}, {"_id": "second", "motor": -0.2}],
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
        "centroid_x_distance",
        "intensity",
        "alignment_cost",
    }
    assert set(outcomes[0]) == {"_id", *metric_names}
    assert all(
        type(outcome[metric]) is float
        for outcome in outcomes
        for metric in metric_names
    )
    assert all(np.isfinite(outcome["alignment_cost"]) for outcome in outcomes)
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

    outcomes = evaluator("acquired", [{"_id": "only", "motor": 0.0}])

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
        metadata={"start": {"blop_suggestions": [{"_id": "first"}, {"_id": "second"}]}},
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
                    {
                        "_id": "first-a",
                        "dcm_roll": -0.2,
                        "m2_yaw": 0.0,
                        "m2_lateral": 0.0,
                    },
                    {
                        "_id": "first-b",
                        "dcm_roll": 0.3,
                        "m2_yaw": 0.0,
                        "m2_lateral": 0.0,
                    },
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
                "blop_suggestions": [
                    {
                        "_id": "second-a",
                        "dcm_roll": 0.1,
                        "m2_yaw": 0.0,
                        "m2_lateral": 0.0,
                    }
                ],
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
        profile=profile,
        resources=resources,
    )
    try:
        figure.canvas.draw()
        row_names = [
            stage.name
            for stage in _image_processing_stages(first_images[0], profile.evaluation)
        ]
        image_axes = [
            axis for axis in figure.axes if axis.get_gid() == "energy-alignment-image"
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
        assert len(image_axes) == len(x_axes) == len(y_axes) == len(row_names) * 3
        assert [
            image_axes[index * 3].get_ylabel().splitlines()[0]
            for index in range(len(row_names))
        ] == row_names

        displayed = np.asarray(image_axes[0].images[0].get_array())
        assert x_axes[0].lines[0].get_ydata() == pytest.approx(displayed.sum(axis=0))
        assert y_axes[0].lines[0].get_xdata() == pytest.approx(displayed.sum(axis=1))
        assert x_axes[0].lines[0].get_xdata() == pytest.approx(
            np.arange(first_images.shape[2])
        )
        assert y_axes[0].lines[0].get_ydata() == pytest.approx(
            np.arange(first_images.shape[1])
        )
        assert len({axis.images[0].get_clim() for axis in image_axes[:3]}) == 1

        final_text = "\n".join(text.get_text() for text in image_axes[-3].texts)
        stats = compute_image_stats(first_images[0], profile.evaluation)
        assert f"FWHM x = {stats.fwhm_x:.3g} px" in final_text
        assert f"FWHM y = {stats.fwhm_y:.3g} px" in final_text
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
        assert "dcm_roll=-0.2" in column_context
        assert "dcm_energy=7114" in column_context
        assert "i0=300" in column_context

        title = figure._suptitle.get_text()
        assert "per-energy" in title and f"profile={profile.name}" in title
        assert first_uid in title and second_uid in title
        assert "reference-full-uid" in title
        assert "µm" not in title
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
                        {
                            "_id": f"energy-{index}",
                            "dcm_roll": index / 10,
                            "m2_yaw": 0.0,
                            "m2_lateral": 0.0,
                        }
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
        profile=profile,
        resources=resources,
    )
    try:
        figure.canvas.draw()
        image_axes = [
            axis for axis in figure.axes if axis.get_gid() == "energy-alignment-image"
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
        row_count = len(_image_processing_stages(gaussian_image(), profile.evaluation))
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
            run["primary"]["data"]["image"].read_count == 1 for run in acquisitions
        )
        assert all(
            run["primary"]["data"]["acquisition_uid"].read_count == 1 for run in outers
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
                profile=profile,
                resources=resources,
            )
    finally:
        plt.close("all")


def test_agent_factory_applies_runtime_profile(make_profile_and_resources):
    profile, resources = make_profile_and_resources()
    first = make_energy_alignment_agent(
        "reference",
        profile=profile,
        resources=resources,
        subscribe_to_dash=False,
    )

    alternate_camera = SynSignal(name="alternate-camera", func=lambda: 1)
    resources.sensors["alternate-camera"] = alternate_camera
    resources.catalog["reference"]["primary"]["data"]["alternate_image"] = Field(
        gaussian_image()
    )
    alternate_evaluation = BeamEvaluationConfig(
        image_field="alternate_image",
        intensity_field="i0",
        x_crop=(8, 29),
        blur_sigma=None,
        upscale_factor=None,
    )
    alternate_cost = AlignmentCostConfig(
        position_tolerance_px=4.0,
        focus_weight=0.25,
        dof_weight=0.2,
    )
    alternate_profile = replace(
        profile,
        camera="alternate-camera",
        dof_bounds={
            "dcm_roll": (-2, 2),
            "m2_yaw": (-3, 3),
            "m2_lateral": (-4, 4),
        },
        search_half_widths={
            "dcm_roll": 0.5,
            "m2_yaw": 1.0,
            "m2_lateral": 1.5,
        },
        evaluation=alternate_evaluation,
        cost=alternate_cost,
        minimum_intensity_fraction=0.75,
        optimization=OptimizationConfig(
            iterations=7,
            initialization_budget=3,
            initialize_with_center=True,
        ),
    )
    generation_strategy_arguments = []
    client_type = type(first.ax_client)
    configure_generation_strategy = client_type.configure_generation_strategy

    def capture_generation_strategy(client, **kwargs):
        generation_strategy_arguments.append(kwargs)
        return configure_generation_strategy(client, **kwargs)

    with patch.object(
        client_type,
        "configure_generation_strategy",
        capture_generation_strategy,
    ):
        second = make_energy_alignment_agent(
            "reference",
            profile=alternate_profile,
            resources=resources,
            subscribe_to_dash=False,
        )

    assert first is not second
    assert second.sensors == [alternate_camera, resources.sensors["i0"]]
    parameters = second.ax_client._experiment.search_space.parameters
    assert {
        name: (parameter.lower, parameter.upper)
        for name, parameter in parameters.items()
    } == {
        "dcm_roll": (-0.25, 0.75),
        "m2_yaw": (-1.0, 1.0),
        "m2_lateral": (-1.5, 1.5),
    }
    assert isinstance(second.evaluation_function, ImageEvaluation)
    assert second.evaluation_function.parameters is alternate_evaluation
    assert second.evaluation_function.cost is alternate_cost
    optimization_config = second.ax_client._experiment.optimization_config
    assert set(optimization_config.objective.metric_names) == {"alignment_cost"}
    assert optimization_config.objective.minimize
    assert [item.expression for item in optimization_config.outcome_constraints] == [
        "intensity >= 0.75 * baseline"
    ]
    assert generation_strategy_arguments == [
        {
            "initialization_budget": 3,
            "initialize_with_center": True,
            "use_existing_trials_for_initialization": False,
        }
    ]
    assert first.acquisition_plan is None
    assert second.acquisition_plan is None


def test_agent_factory_restores_checkpointed_optimizer(
    tmp_path,
    make_profile_and_resources,
):
    profile, resources = make_profile_and_resources()
    checkpoint_path = tmp_path / "agent.json"
    original = make_energy_alignment_agent(
        "reference",
        profile=profile,
        resources=resources,
        checkpoint_path=checkpoint_path,
        subscribe_to_dash=False,
    )
    original.ingest(
        [
            {
                "dcm_roll": 0.25,
                "m2_yaw": 0.0,
                "m2_lateral": 0.0,
                "alignment_cost": 0.5,
                "intensity": 1_000_000.0,
                "_id": "baseline",
            }
        ]
    )

    _write_agent_checkpoint(original, checkpoint_path)
    restored = make_energy_alignment_agent(
        "reference",
        profile=profile,
        resources=resources,
        checkpoint_path=checkpoint_path,
        resume=True,
        subscribe_to_dash=False,
    )

    assert restored.checkpoint_path == str(checkpoint_path)
    assert "baseline" in set(restored.ax_client.summarize()["arm_name"])
    assert restored.to_optimization_problem().optimizer.should_stop() == (False, None)


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
        result = run_engine(acquire_target_position([camera, ion_chamber, motor]))

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
    assert agent_metadata["profile"] == "xas-si111"
    assert agent_metadata["iterations"] == 2
    assert [dof["name"] for dof in agent_metadata["dofs"]] == [
        "dcm_roll",
        "m2_yaw",
        "m2_lateral",
    ]
    assert [dof["bounds"] for dof in agent_metadata["dofs"]] == [
        [-1, 1],
        [-1, 1],
        [-1, 1],
    ]
    assert agent_metadata["sensors"] == ["camera", "i0"]
    assert agent_metadata["objectives"] == ["alignment_cost"]
    assert agent_metadata["outcome_constraints"] == ["intensity >= 0.5 * baseline"]
    assert agent_metadata["cost"] == {
        "position_tolerance_px": 5.0,
        "focus_weight": 0.5,
        "dof_weight": 0.1,
    }
    assert agent_metadata["search_half_widths"] is None


def test_dash_callback_builds_app(make_profile_and_resources):
    profile, resources = make_profile_and_resources()
    agent = make_energy_alignment_agent(
        "reference",
        profile=profile,
        resources=resources,
        subscribe_to_dash=False,
    )

    callback = SurrogateModelDashCallback(agent)
    callback.event(
        {"data": {"dcm_roll": 0.25, "m2_yaw": 0.0, "m2_lateral": 0.0}}
    )
    figure = callback.compute_figure("dcm_roll", "dcm_roll", "alignment_cost")
    app = callback.build_app()

    assert callback.dof_names == ["dcm_roll", "m2_yaw", "m2_lateral"]
    assert callback.objective_names == ["alignment_cost"]
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
    agent_events = []

    class FakeAgent:
        def __init__(self, acquisition_plan):
            self.acquisition_plan = acquisition_plan

        def acquire_baseline(self, nominal_dof_values):
            agent_events.append(("baseline", dict(nominal_dof_values)))
            yield from null()

        def optimize(self, iterations):
            agent_events.append(("optimize", iterations))
            yield from self.acquisition_plan(
                [
                    {
                        "_id": "test",
                        "dcm_roll": 0.0,
                        "m2_yaw": 0.0,
                        "m2_lateral": 0.0,
                    }
                ],
                [
                    resources.actuators["dcm_roll"],
                    resources.actuators["m2_yaw"],
                    resources.actuators["m2_lateral"],
                ],
                [resources.sensors["camera"], resources.sensors["i0"]],
            )

        def get_best_points(self):
            return [
                (
                    0,
                    {"dcm_roll": 0.0, "m2_yaw": 0.0, "m2_lateral": 0.0},
                    {"centroid_distance": (0.0, 0.0)},
                )
            ]

    reference_image = Field(gaussian_image())
    catalog = {
        "target": {
            "primary": {
                "data": {
                    "image": reference_image,
                    "dcm_roll": Field(0.25),
                    "m2_yaw": Field(0.0),
                    "m2_lateral": Field(0.0),
                }
            }
        }
    }
    lifecycle = []
    acquired_readables = []
    change_edge_calls = []

    def acquire_target(readables):
        lifecycle.append("acquire")
        acquired_readables.append(tuple(readables))
        yield from null()
        return "target"

    def change_edge(energy, **kwargs):
        lifecycle.append(f"edge:{energy}")
        change_edge_calls.append((energy, kwargs))
        yield from null()

    profile, resources = make_profile_and_resources(
        catalog=catalog,
        change_edge_plan=change_edge,
    )
    change_edge_kwargs = {
        "focus": False,
        "no_hslits": False,
        "mirror": True,
        "xrd": True,
        "bender": False,
    }
    profile = replace(profile, change_edge_kwargs=change_edge_kwargs)
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
    assert change_edge_calls == [
        ("Fe", change_edge_kwargs),
        ("Cu", change_edge_kwargs),
    ]
    assert acquired_readables == [
        (
            resources.sensors["camera"],
            resources.sensors["i0"],
            resources.actuators["dcm_roll"],
            resources.actuators["m2_yaw"],
            resources.actuators["m2_lateral"],
        )
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
    nominal_dof_values = {"dcm_roll": 0.25, "m2_yaw": 0.0, "m2_lateral": 0.0}
    assert agent_events == [
        ("baseline", nominal_dof_values),
        ("optimize", 2),
        ("baseline", nominal_dof_values),
        ("optimize", 2),
    ]


def test_search_uses_supplied_target_reference(
    make_profile_and_resources,
    monkeypatch,
):
    class FakeAgent:
        def acquire_baseline(self, nominal_dof_values):
            yield from null()

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
        catalog={
            "target": run_with_fields(
                image=gaussian_image(),
                dcm_roll=0.25,
                m2_yaw=0.0,
                m2_lateral=0.0,
            )
        },
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


def test_search_honors_deferred_pause_after_optimization(
    make_profile_and_resources,
    monkeypatch,
):
    pause_requested = False

    class FakeAgent:
        def acquire_baseline(self, nominal_dof_values):
            yield from null()

        def optimize(self, iterations):
            nonlocal pause_requested
            if not pause_requested:
                pause_requested = True
                yield from deferred_pause()
            yield from null()

        def get_best_points(self):
            return [
                (
                    0,
                    {"dcm_roll": 0.0, "m2_yaw": 0.0, "m2_lateral": 0.0},
                    {"centroid_distance": (0.0, 0.0)},
                )
            ]

    profile, resources = make_profile_and_resources()
    monkeypatch.setattr(
        optimization_module,
        "make_energy_alignment_agent",
        lambda *args, **kwargs: FakeAgent(),
    )
    run_engine = RunEngine({}, call_returns_result=True)
    plan = search_for_optimal_positions(
        ["Fe"],
        reference_scan_uid="reference",
        profile=profile,
        resources=resources,
    )

    with pytest.raises(RunEngineInterrupted):
        run_engine(plan)

    assert run_engine.state == "paused"
    assert resources.prompt_state.prompt is False

    result = run_engine.resume()

    assert result.plan_result == {
        "Fe": [
            (
                0,
                {"dcm_roll": 0.0, "m2_yaw": 0.0, "m2_lateral": 0.0},
                {"centroid_distance": (0.0, 0.0)},
            )
        ]
    }
    assert resources.prompt_state.prompt is True


def test_search_resumes_latest_incomplete_energy_from_agent_checkpoint(
    tmp_path,
    make_profile_and_resources,
    monkeypatch,
):
    events = []
    fail_during_cu = True

    class FakeAgent:
        def __init__(self, energy, checkpoint_path, completed_iterations=0):
            self.energy = energy
            self.checkpoint_path = checkpoint_path
            self.completed_iterations = completed_iterations

        def acquire_baseline(self, nominal_dof_values):
            events.append(("baseline", self.energy))
            yield from null()

        def optimize(self, iterations):
            nonlocal fail_during_cu
            start = self.completed_iterations
            events.append(("optimize", self.energy, start, iterations))
            yield from null()
            if self.energy == "Cu" and start == 2 and fail_during_cu:
                raise RuntimeError("interrupted optimization")
            self.completed_iterations += iterations

        def checkpoint(self):
            with open(self.checkpoint_path, "wb") as stream:
                pickle.dump(self.completed_iterations, stream)

        def get_best_points(self):
            return [
                (
                    0,
                    {
                        "dcm_roll": float(self.completed_iterations),
                        "m2_yaw": 0.0,
                        "m2_lateral": 0.0,
                    },
                    {"centroid_distance": (0.0, 0.0)},
                )
            ]

    def make_agent(
        reference_scan_uid,
        *,
        acquisition_plan,
        checkpoint_path=None,
        resume=False,
        **kwargs,
    ):
        assert reference_scan_uid == "reference"
        energy = acquisition_plan.keywords["md"]["BMM_agent"]["requested_energy"]
        completed_iterations = 0
        if resume:
            with open(checkpoint_path, "rb") as stream:
                completed_iterations = pickle.load(stream)
        events.append(("agent", energy, resume, completed_iterations))
        return FakeAgent(energy, checkpoint_path, completed_iterations)

    edge_changes = []

    def change_edge(energy, **kwargs):
        edge_changes.append(energy)
        yield from null()

    profile, resources = make_profile_and_resources(change_edge_plan=change_edge)
    monkeypatch.setattr(
        optimization_module,
        "make_energy_alignment_agent",
        make_agent,
    )
    checkpoint_directory = tmp_path / "optimization-checkpoints"
    energy_map_filename = tmp_path / "energy-map.pickle"

    with pytest.raises(RuntimeError, match="interrupted optimization"):
        RunEngine({})(
            search_for_optimal_positions(
                ["Fe", "Cu"],
                reference_scan_uid="reference",
                energy_map_filename=energy_map_filename,
                checkpoint_directory=checkpoint_directory,
                checkpoint_interval=2,
                iterations=4,
                profile=profile,
                resources=resources,
            )
        )

    with energy_map_filename.open("rb") as stream:
        assert pickle.load(stream) == {
            "Fe": [
                (
                    0,
                    {"dcm_roll": 4.0, "m2_yaw": 0.0, "m2_lateral": 0.0},
                    {"centroid_distance": (0.0, 0.0)},
                )
            ]
        }
    assert resources.prompt_state.prompt is True

    fail_during_cu = False
    result = RunEngine({}, call_returns_result=True)(
        search_for_optimal_positions(
            ["Fe", "Cu"],
            reference_scan_uid="reference",
            energy_map_filename=energy_map_filename,
            checkpoint_directory=checkpoint_directory,
            checkpoint_interval=2,
            iterations=3,
            resume=True,
            profile=profile,
            resources=resources,
        )
    )

    assert [event for event in events if event[0] == "baseline"] == [
        ("baseline", "Fe"),
        ("baseline", "Cu"),
    ]
    assert [event for event in events if event[0] == "agent"] == [
        ("agent", "Fe", False, 0),
        ("agent", "Cu", False, 0),
        ("agent", "Cu", True, 2),
    ]
    assert [event[2:] for event in events if event[:2] == ("optimize", "Cu")] == [
        (0, 2),
        (2, 2),
        (2, 2),
        (4, 1),
    ]
    assert edge_changes == ["Fe", "Cu", "Cu"]
    assert result.plan_result == {
        "Fe": [
            (
                0,
                {"dcm_roll": 4.0, "m2_yaw": 0.0, "m2_lateral": 0.0},
                {"centroid_distance": (0.0, 0.0)},
            )
        ],
        "Cu": [
            (
                0,
                {"dcm_roll": 5.0, "m2_yaw": 0.0, "m2_lateral": 0.0},
                {"centroid_distance": (0.0, 0.0)},
            )
        ],
    }
    with energy_map_filename.open("rb") as stream:
        assert pickle.load(stream) == result.plan_result
    assert sorted(path.name for path in checkpoint_directory.iterdir()) == [
        "agent-Cu.json",
        "agent-Fe.json",
    ]

    event_count = len(events)
    completed = RunEngine({}, call_returns_result=True)(
        search_for_optimal_positions(
            ["Fe", "Cu"],
            reference_scan_uid="reference",
            energy_map_filename=energy_map_filename,
            checkpoint_directory=checkpoint_directory,
            iterations=10,
            resume=True,
            profile=profile,
            resources=resources,
        )
    )
    assert completed.plan_result == result.plan_result
    assert len(events) == event_count


def test_compute_alignment_cost_matches_reference_formula():
    cost = compute_alignment_cost(
        centroid_x=105.0,
        reference_centroid_x=100.0,
        fwhm_x=10.0,
        reference_fwhm_x=10.0,
        dof_values={"motor": 0.5},
        nominal_dof_values={"motor": 0.5},
        dof_half_ranges={"motor": 10.0},
        config=AlignmentCostConfig(),
    )
    # (5 / 5) ** 2 + 0.5 * (10 / 10) + 0.1 * 0 == 1.5
    assert cost == pytest.approx(1.5)


def test_compute_alignment_cost_increases_with_offset_and_dof_deviation():
    fixed = dict(
        reference_centroid_x=100.0,
        fwhm_x=10.0,
        reference_fwhm_x=10.0,
        nominal_dof_values={"motor": 0.0},
        dof_half_ranges={"motor": 10.0},
        config=AlignmentCostConfig(),
    )
    aligned = compute_alignment_cost(
        centroid_x=100.0, dof_values={"motor": 0.0}, **fixed
    )
    off_position = compute_alignment_cost(
        centroid_x=110.0, dof_values={"motor": 0.0}, **fixed
    )
    off_nominal = compute_alignment_cost(
        centroid_x=100.0, dof_values={"motor": 8.0}, **fixed
    )
    assert off_position > aligned
    assert off_nominal > aligned


def test_unusable_beam_error_subclasses_value_error():
    assert issubclass(UnusableBeamError, ValueError)


@pytest.mark.parametrize(
    ("image", "parameters"),
    [
        pytest.param(
            np.ones((31, 41)),
            BeamEvaluationConfig("image", "i0", (8, 29), (4, 21)),
            id="constant-image",
        ),
        pytest.param(
            gaussian_image(center_x=8),
            BeamEvaluationConfig("image", "i0", (8, 29), (4, 21)),
            id="beam-at-crop-boundary",
        ),
    ],
)
def test_compute_image_stats_raises_unusable_beam_error(image, parameters):
    with pytest.raises(UnusableBeamError):
        compute_image_stats(image, parameters)


@pytest.mark.parametrize(
    ("image", "parameters"),
    [
        pytest.param(
            gaussian_image(),
            BeamEvaluationConfig("image", "i0", (8, 8), (4, 21)),
            id="empty-x-crop",
        ),
        pytest.param(
            np.full((31, 41), np.nan),
            BeamEvaluationConfig("image", "i0", (8, 29), (4, 21)),
            id="non-finite-pixels",
        ),
    ],
)
def test_structural_errors_are_not_unusable_beam_errors(image, parameters):
    with pytest.raises(ValueError) as excinfo:
        compute_image_stats(image, parameters)
    assert not isinstance(excinfo.value, UnusableBeamError)


def test_image_evaluation_reports_unusable_frame_as_partial_observation():
    evaluator, catalog = make_image_evaluator()
    catalog["acquired"] = run_with_fields(
        image=np.ones((31, 41))[np.newaxis, ...],
        i0=np.array([1_250_000.0]),
    )

    [outcome] = evaluator("acquired", [{"_id": "only", "motor": 0.3}])

    assert outcome["intensity"] == 1_250_000.0
    assert np.isnan(outcome["alignment_cost"])
    assert np.isnan(outcome["centroid_distance"])
    assert np.isnan(outcome["centroid_x_distance"])
    assert np.isnan(outcome["fwhm_x"])
    assert set(outcome) == {
        "_id",
        "fwhm_x",
        "fwhm_y",
        "centroid_x",
        "centroid_y",
        "centroid_distance",
        "centroid_x_distance",
        "intensity",
        "alignment_cost",
    }


@pytest.mark.parametrize(
    ("nominal", "half_width", "expected_bounds"),
    [
        (0.4, 0.5, (-0.1, 0.9)),
        (0.9, 0.5, (0.4, 1.0)),
        (-0.9, 0.5, (-1.0, -0.4)),
    ],
    ids=["interior", "clamped-upper", "clamped-lower"],
)
def test_resolve_search_space_recenters_and_clamps(
    make_profile_and_resources, nominal, half_width, expected_bounds
):
    profile, resources = make_profile_and_resources(
        catalog={
            "target": run_with_fields(
                image=gaussian_image(),
                dcm_roll=nominal,
                m2_yaw=0.0,
                m2_lateral=0.0,
            )
        },
    )
    profile = replace(profile, search_half_widths={"dcm_roll": half_width})

    resolved_dofs, nominal_values, half_ranges = _resolve_search_space(
        resources.catalog, "target", resources, profile
    )

    assert nominal_values == {
        "dcm_roll": pytest.approx(nominal),
        "m2_yaw": pytest.approx(0.0),
        "m2_lateral": pytest.approx(0.0),
    }
    assert resolved_dofs[0].bounds == pytest.approx(expected_bounds)
    assert half_ranges["dcm_roll"] == pytest.approx(
        (expected_bounds[1] - expected_bounds[0]) / 2
    )


def test_resolve_search_space_keeps_bounds_without_half_widths(
    make_profile_and_resources,
):
    profile, resources = make_profile_and_resources(
        catalog={
            "target": run_with_fields(
                image=gaussian_image(),
                dcm_roll=0.4,
                m2_yaw=0.0,
                m2_lateral=0.0,
            )
        },
    )

    resolved_dofs, _, half_ranges = _resolve_search_space(
        resources.catalog, "target", resources, profile
    )

    assert resolved_dofs[0].bounds == (-1, 1)
    assert half_ranges["dcm_roll"] == pytest.approx(1.0)
