from dataclasses import replace
import pickle
from types import SimpleNamespace

from blop.ax import Objective, RangeDOF
from bluesky import RunEngine
from bluesky.plan_stubs import close_run, mv, null, open_run
import numpy as np
from ophyd.sim import SynAxis, SynSignal
import pytest

import BMM.optimization as optimization_module
from BMM.optimization import (
    ENERGY_ALIGNMENT_PROFILES,
    ENERGY_RANGE_ALIGNMENT,
    PER_ENERGY_ALIGNMENT,
    BeamEvaluationConfig,
    EnergyAlignmentResources,
    EnergyRangeEvaluation,
    ImageEvaluation,
    SurrogateModelDashCallback,
    _optimization_metadata,
    _full_width_half_maximum,
    _preprocess_image,
    _write_energy_map,
    compute_image_stats,
    compute_stats,
    get_energy_alignment_profile,
    make_energy_alignment_agent,
    make_energy_range_alignment_agent,
    make_energy_scan_acquisition_plan,
    optimization_metadata_wrapper,
    search_for_optimal_positions,
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


def make_range_profile_and_resources(*, energy_readable=True):
    parameters = BeamEvaluationConfig(
        image_field="image",
        intensity_field="i0",
        blur_sigma=None,
        upscale_factor=None,
    )
    profile = replace(
        ENERGY_RANGE_ALIGNMENT,
        name="test-range",
        evaluation=parameters,
        optimization=replace(ENERGY_RANGE_ALIGNMENT.optimization, iterations=1),
    )
    energy = SynAxis(name="energy")
    dcm_roll = SynAxis(name="dcm_roll")
    m2_yaw = SynAxis(name="m2_yaw")
    m2_lateral = SynAxis(name="m2_lateral")
    edge_changes = []
    edge_energies = {"Fe": 7112.0, "Cu": 8979.0, "Zn": 9659.0}

    def change_edge(element, **kwargs):
        edge_changes.append((element, kwargs))
        yield from mv(
            energy,
            edge_energies[element],
            dcm_roll,
            -99.0,
            m2_yaw,
            -99.0,
            m2_lateral,
            -99.0,
        )

    def camera_image():
        return gaussian_image(center_x=17.0 if energy.position == 7112.0 else 19.0)

    def i0_value():
        return 1_250_000.0 if energy.position == 7112.0 else 2_500_000.0

    catalog = {"reference": run_with_fields(image=gaussian_image(center_x=18.0))}
    resources = EnergyAlignmentResources(
        catalog=catalog,
        actuators={
            "dcm_roll": dcm_roll,
            "m2_yaw": m2_yaw,
            "m2_lateral": m2_lateral,
        },
        sensors={
            "camera": SynSignal(name="camera", func=camera_image),
            "i0": SynSignal(name="i0", func=i0_value),
        },
        change_edge_plan=change_edge,
        prompt_state=SimpleNamespace(prompt=True),
        energy_readable=energy if energy_readable else None,
    )
    return profile, resources, edge_changes


def materialize_default_acquire_runs(catalog):
    starts = {}
    descriptor_runs = {}
    events = {}

    def subscriber(name, doc):
        if name == "start" and doc.get("run_key") == "default_acquire":
            starts[doc["uid"]] = doc
            events[doc["uid"]] = []
        elif name == "descriptor" and doc.get("run_start") in starts:
            descriptor_runs[doc["uid"]] = doc["run_start"]
        elif name == "event" and doc["descriptor"] in descriptor_runs:
            events[descriptor_runs[doc["descriptor"]]].append(doc["data"])
        elif name == "stop" and doc.get("run_start") in starts:
            uid = doc["run_start"]
            run_events = events[uid]
            catalog[uid] = run_with_fields(
                metadata={"start": starts[uid]},
                image=np.stack([event["camera"] for event in run_events]),
                i0=np.array([event["i0"] for event in run_events]),
            )

    return subscriber


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


def test_compute_image_stats_preprocesses_in_native_coordinates():
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

    processed, x_coordinates, y_coordinates = _preprocess_image(image, parameters)
    assert processed.shape == (68, 84)
    assert np.diff(x_coordinates) == pytest.approx(0.25)
    assert np.diff(y_coordinates) == pytest.approx(0.25)
    assert x_coordinates.mean() == pytest.approx(18)
    assert y_coordinates.mean() == pytest.approx(12)


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

    processed, x_coordinates, y_coordinates = _preprocess_image(image, parameters)
    stats = compute_image_stats(image, parameters)

    assert processed.shape == image.shape
    assert np.array_equal(x_coordinates, np.arange(image.shape[1]))
    assert np.array_equal(y_coordinates, np.arange(image.shape[0]))
    assert np.count_nonzero(processed) == 1
    assert processed[3, 4] == 10
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


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"blur_sigma": 0}, "blur_sigma must be finite and positive"),
        ({"blur_sigma": np.inf}, "blur_sigma must be finite and positive"),
        (
            {"upscale_factor": True},
            "upscale_factor must be a non-boolean integer of at least 2",
        ),
        (
            {"upscale_factor": 1},
            "upscale_factor must be a non-boolean integer of at least 2",
        ),
    ],
)
def test_compute_image_stats_rejects_invalid_config(changes, message):
    parameters = BeamEvaluationConfig("image", "i0", (8, 29), (4, 21))

    with pytest.raises(ValueError, match=message):
        compute_image_stats(gaussian_image(), replace(parameters, **changes))


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


@pytest.mark.parametrize(
    "outcome",
    [
        "centroid_x_offset_mean_um",
        "centroid_x_std_um",
        "centroid_x_span_um",
        "centroid_x_rmse_um",
        "fwhm_x_mean_um",
        "fwhm_x_std_um",
        "fwhm_x_rms_um",
        "centroid_x_rmse_normalized",
        "fwhm_x_rms_normalized",
        "intensity_min",
        "intensity_mean",
    ],
)
def test_profile_accepts_energy_scan_outcomes(outcome):
    profile = replace(
        ENERGY_RANGE_ALIGNMENT,
        name=f"{outcome}-objective",
        objectives=(Objective(name=outcome, minimize=True),),
    )

    assert get_energy_alignment_profile(profile) is profile


def test_profile_rejects_outcomes_from_wrong_evaluator():
    profile = replace(
        PER_ENERGY_ALIGNMENT,
        name="wrong-evaluator-outcome",
        objectives=(Objective(name="centroid_x_rmse_normalized", minimize=True),),
    )

    with pytest.raises(ValueError, match="single-image"):
        get_energy_alignment_profile(profile)


def test_profile_rejects_unsupported_evaluation_outcome():
    profile = replace(
        PER_ENERGY_ALIGNMENT,
        name="unsupported-outcome",
        objectives=(Objective(name="beam_width", minimize=True),),
    )

    with pytest.raises(ValueError, match="beam_width"):
        get_energy_alignment_profile(profile)


def make_image_evaluator():
    parameters = BeamEvaluationConfig(
        image_field="image",
        intensity_field="i0",
        x_crop=(4, 37),
    )
    catalog = {"reference": run_with_fields(image=gaussian_image())}
    return ImageEvaluation(catalog, "reference", parameters), catalog


def make_energy_range_evaluator(*, energy_count=2, micrometers_per_pixel=10.0):
    parameters = BeamEvaluationConfig(
        image_field="image",
        intensity_field="i0",
        blur_sigma=None,
        upscale_factor=None,
    )
    catalog = {"reference": run_with_fields(image=gaussian_image(center_x=18.0))}
    return (
        EnergyRangeEvaluation(
            catalog,
            "reference",
            parameters,
            energy_count=energy_count,
            micrometers_per_pixel=micrometers_per_pixel,
        ),
        catalog,
        parameters,
    )


def test_energy_range_evaluation_aggregates_energy_block_metrics():
    evaluator, catalog, parameters = make_energy_range_evaluator()
    reference_image = catalog["reference"]["primary"]["data"]["image"]
    first_image = gaussian_image(center_x=17.0, sigma_x=2.0)
    second_image = gaussian_image(center_x=19.0, sigma_x=4.0)
    catalog["acquired"] = run_with_fields(
        image=np.stack((first_image, second_image)),
        i0=np.array([1_250_000.0, 2_500_000.0]),
    )

    [outcome] = evaluator("acquired", [{"_id": "scan"}])

    metric_names = {
        "centroid_x_offset_mean_um",
        "centroid_x_std_um",
        "centroid_x_span_um",
        "centroid_x_rmse_um",
        "fwhm_x_mean_um",
        "fwhm_x_std_um",
        "fwhm_x_rms_um",
        "centroid_x_rmse_normalized",
        "fwhm_x_rms_normalized",
        "intensity_min",
        "intensity_mean",
    }
    assert set(outcome) == {"_id", *metric_names}
    assert all(type(outcome[metric]) is float for metric in metric_names)
    assert outcome["_id"] == "scan"
    assert outcome["centroid_x_offset_mean_um"] == pytest.approx(0.0, abs=1e-10)
    assert outcome["centroid_x_std_um"] == pytest.approx(10.0, abs=1e-10)
    assert outcome["centroid_x_span_um"] == pytest.approx(20.0, abs=1e-10)
    assert outcome["centroid_x_rmse_um"] == pytest.approx(10.0, abs=1e-10)
    assert outcome["centroid_x_rmse_normalized"] == pytest.approx(0.2, abs=1e-10)
    fwhm_um = np.array(
        [
            compute_image_stats(first_image, parameters).fwhm_x,
            compute_image_stats(second_image, parameters).fwhm_x,
        ]
    ) * 10.0
    reference_fwhm_um = (
        compute_image_stats(reference_image.value, parameters).fwhm_x * 10.0
    )
    assert outcome["fwhm_x_mean_um"] == pytest.approx(float(np.mean(fwhm_um)))
    assert outcome["fwhm_x_std_um"] == pytest.approx(float(np.std(fwhm_um)))
    assert outcome["fwhm_x_rms_um"] == pytest.approx(
        float(np.sqrt(np.mean(fwhm_um**2)))
    )
    assert outcome["fwhm_x_rms_normalized"] == pytest.approx(
        float(np.sqrt(np.mean(fwhm_um**2))) / reference_fwhm_um
    )
    assert outcome["intensity_min"] == 1_250_000.0
    assert outcome["intensity_mean"] == 1_875_000.0
    assert reference_image.read_count == 1
    assert catalog["acquired"]["primary"]["data"]["image"].read_count == 1
    assert catalog["acquired"]["primary"]["data"]["i0"].read_count == 1


def test_energy_range_evaluation_reorders_two_suggestion_energy_blocks():
    evaluator, catalog, _ = make_energy_range_evaluator()
    catalog["acquired"] = run_with_fields(
        metadata={
            "start": {"blop_suggestions": [{"_id": "second"}, {"_id": "first"}]}
        },
        image=np.stack(
            (
                gaussian_image(center_x=20.0),
                gaussian_image(center_x=21.0),
                gaussian_image(center_x=16.0),
                gaussian_image(center_x=18.0),
            )
        ),
        i0=np.array([2_000_000.0, 2_200_000.0, 1_100_000.0, 1_300_000.0]),
    )

    outcomes = evaluator(
        "acquired",
        [{"_id": "first"}, {"_id": "second"}],
    )

    assert [outcome["_id"] for outcome in outcomes] == ["first", "second"]
    assert outcomes[0]["centroid_x_offset_mean_um"] == pytest.approx(-10.0)
    assert outcomes[0]["intensity_min"] == 1_100_000.0
    assert outcomes[0]["intensity_mean"] == 1_200_000.0
    assert outcomes[1]["centroid_x_offset_mean_um"] == pytest.approx(25.0)
    assert outcomes[1]["intensity_min"] == 2_000_000.0
    assert outcomes[1]["intensity_mean"] == 2_100_000.0


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({}, "Batch evaluation requires blop_suggestions metadata"),
        (
            {"start": {"blop_suggestions": [{"_id": "first"}, {"_id": "other"}]}},
            "metadata IDs do not match supplied suggestion IDs",
        ),
        (
            {"start": {"blop_suggestions": [{"_id": "first"}, {"_id": "first"}]}},
            "metadata IDs do not match supplied suggestion IDs",
        ),
    ],
    ids=["missing", "mismatched", "duplicate"],
)
def test_energy_range_evaluation_rejects_invalid_batch_metadata(metadata, message):
    evaluator, catalog, _ = make_energy_range_evaluator()
    catalog["acquired"] = run_with_fields(
        metadata=metadata,
        image=np.stack([gaussian_image() for _ in range(4)]),
        i0=np.full(4, 1_250_000.0),
    )

    with pytest.raises(ValueError, match=message):
        evaluator("acquired", [{"_id": "first"}, {"_id": "second"}])


@pytest.mark.parametrize(
    ("images", "intensities", "field"),
    [
        (np.stack([gaussian_image() for _ in range(3)]), np.full(4, 1.0), "image"),
        (np.stack([gaussian_image() for _ in range(4)]), np.full(3, 1.0), "i0"),
        (np.stack([gaussian_image() for _ in range(4)]), np.ones((4, 1)), "i0"),
    ],
    ids=["image-count", "ion-count", "non-scalar-ion-samples"],
)
def test_energy_range_evaluation_rejects_mismatched_payload_shapes(
    images,
    intensities,
    field,
):
    evaluator, catalog, _ = make_energy_range_evaluator()
    catalog["acquired"] = run_with_fields(
        metadata={
            "start": {"blop_suggestions": [{"_id": "first"}, {"_id": "second"}]}
        },
        image=images,
        i0=intensities,
    )

    with pytest.raises(ValueError, match="Received 2 suggestions with 2") as exc_info:
        evaluator("acquired", [{"_id": "first"}, {"_id": "second"}])
    assert repr(field) in str(exc_info.value)


@pytest.mark.parametrize("energy_count", [1, 0, True, 1.5])
def test_energy_range_evaluation_rejects_invalid_energy_count(energy_count):
    with pytest.raises(ValueError, match="energy_count"):
        make_energy_range_evaluator(energy_count=energy_count)


@pytest.mark.parametrize("micrometers_per_pixel", [0, -1, np.nan, np.inf, True, "10"])
def test_energy_range_evaluation_rejects_invalid_camera_scale(micrometers_per_pixel):
    with pytest.raises(ValueError, match="micrometers_per_pixel"):
        make_energy_range_evaluator(micrometers_per_pixel=micrometers_per_pixel)


def test_energy_range_evaluation_propagates_per_image_analysis_errors():
    evaluator, catalog, _ = make_energy_range_evaluator()
    catalog["acquired"] = run_with_fields(
        image=np.stack((gaussian_image(), np.zeros((31, 41)))),
        i0=np.array([1_250_000.0, 1_250_000.0]),
    )

    with pytest.raises(ValueError, match="positive signal"):
        evaluator("acquired", [{"_id": "scan"}])


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
    ("metadata", "message"),
    [
        ({}, "Batch evaluation requires blop_suggestions metadata"),
        (
            {
                "start": {
                    "blop_suggestions": [{"_id": "first"}, {"_id": "other"}]
                }
            },
            "metadata IDs do not match supplied suggestion IDs",
        ),
        (
            {
                "start": {
                    "blop_suggestions": [{"_id": "first"}, {"_id": "first"}]
                }
            },
            "metadata IDs do not match supplied suggestion IDs",
        ),
    ],
    ids=["missing", "mismatched", "duplicate"],
)
def test_image_evaluation_rejects_invalid_batch_metadata(metadata, message):
    evaluator, catalog = make_image_evaluator()
    catalog["acquired"] = run_with_fields(
        metadata=metadata,
        image=np.stack((gaussian_image(), gaussian_image(center_x=20))),
        i0=np.array([1_250_000.0, 2_500_000.0]),
    )

    with pytest.raises(ValueError, match=message):
        evaluator("acquired", [{"_id": "first"}, {"_id": "second"}])




@pytest.mark.parametrize(
    ("images", "intensities", "field"),
    [
        (
            np.stack((gaussian_image(),)),
            np.array([1_250_000.0, 2_500_000.0]),
            "image",
        ),
        (
            np.stack((gaussian_image(), gaussian_image(center_x=20))),
            np.array([1_250_000.0]),
            "i0",
        ),
        (
            np.stack((gaussian_image(), gaussian_image(center_x=20))),
            np.ones((2, 2)),
            "i0",
        ),
    ],
    ids=["image-count", "ion-count", "non-scalar-ion-samples"],
)
def test_image_evaluation_rejects_mismatched_payload_shapes(
    images,
    intensities,
    field,
):
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

    with pytest.raises(ValueError, match="Received 2 suggestions") as exc_info:
        evaluator("acquired", [{"_id": "first"}, {"_id": "second"}])
    assert repr(field) in str(exc_info.value)


@pytest.mark.parametrize("invalid_intensity", [np.nan, np.inf, -np.inf])
def test_image_evaluation_rejects_non_finite_ion_readings(invalid_intensity):
    evaluator, catalog = make_image_evaluator()
    catalog["acquired"] = run_with_fields(
        metadata={
            "start": {
                "blop_suggestions": [{"_id": "first"}, {"_id": "second"}]
            }
        },
        image=np.stack((gaussian_image(), gaussian_image(center_x=20))),
        i0=np.array([1_250_000.0, invalid_intensity]),
    )

    with pytest.raises(ValueError, match="finite values"):
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


def test_compute_stats_rejects_non_scalar_ion_reading(make_profile_and_resources):
    run = run_with_fields(image=gaussian_image(), i0=np.array([1.0, 2.0]))
    profile, resources = make_profile_and_resources(catalog={"acquired": run})

    with pytest.raises(ValueError, match="one finite scalar"):
        compute_stats("acquired", profile=profile, resources=resources)


def test_agent_factory_returns_fresh_agents(make_profile_and_resources):
    profile, resources = make_profile_and_resources()

    first = make_energy_alignment_agent(
        "reference", profile=profile, resources=resources
    )
    second = make_energy_alignment_agent(
        "reference", profile=profile, resources=resources
    )

    assert first is not second
    assert profile.dofs[0].actuator == "motor"
    assert profile.dofs[0].step_size == 0.1
    assert first.acquisition_plan is None
    assert second.acquisition_plan is None


def test_per_energy_agent_factory_rejects_energy_scan_profile(make_profile_and_resources):
    _, resources = make_profile_and_resources()

    with pytest.raises(ValueError, match="single-image profile"):
        make_energy_alignment_agent(
            "reference", profile=ENERGY_RANGE_ALIGNMENT, resources=resources
        )



@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"configuration_name": "  "}, "configuration_name"),
        ({"elements": ["Fe"]}, "at least two"),
        ({"micrometers_per_pixel": 0.0}, "micrometers_per_pixel"),
        ({"profile": PER_ENERGY_ALIGNMENT}, "energy-scan profile"),
    ],
    ids=["blank-configuration", "short-elements", "bad-scale", "wrong-profile"],
)
def test_energy_range_agent_factory_rejects_invalid_inputs(kwargs, message):
    profile, resources, _ = make_range_profile_and_resources()
    arguments = {
        "elements": ["Fe", "Cu"],
        "configuration_name": "si111-xas",
        "micrometers_per_pixel": 10.0,
        "profile": profile,
        "resources": resources,
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError, match=message):
        make_energy_range_alignment_agent("reference", **arguments)


def test_energy_range_agent_factory_rejects_missing_energy_readable():
    profile, resources, _ = make_range_profile_and_resources(energy_readable=False)

    with pytest.raises(ValueError, match="energy_readable"):
        make_energy_range_alignment_agent(
            "reference",
            ["Fe", "Cu"],
            configuration_name="si111-xas",
            micrometers_per_pixel=10.0,
            profile=profile,
            resources=resources,
        )


def test_energy_range_agent_factory_samples_manual_suggestion_end_to_end():
    profile, resources, edge_changes = make_range_profile_and_resources()
    agent = make_energy_range_alignment_agent(
        "reference",
        ["Fe", "Cu"],
        configuration_name="si111-xas",
        micrometers_per_pixel=10.0,
        xrd=True,
        profile=profile,
        resources=resources,
    )
    second_agent = make_energy_range_alignment_agent(
        "reference",
        ["Fe", "Fe", "Cu"],
        configuration_name="si311-xas",
        micrometers_per_pixel=5.0,
        profile=profile,
        resources=resources,
    )
    documents = []
    run_engine = RunEngine({}, call_returns_result=True)
    run_engine.subscribe(materialize_default_acquire_runs(resources.catalog))
    run_engine.subscribe(lambda name, doc: documents.append((name, doc)))

    run_engine(
        agent.sample_suggestions(
            [{"dcm_roll": 0.1, "m2_yaw": 0.2, "m2_lateral": 0.3}]
        )
    )

    assert agent is not second_agent
    assert agent.acquisition_plan is not second_agent.acquisition_plan
    assert agent.evaluation_function.energy_count == 2
    assert second_agent.evaluation_function.energy_count == 3
    assert agent.evaluation_function.micrometers_per_pixel == 10.0
    assert second_agent.evaluation_function.micrometers_per_pixel == 5.0
    assert [element for element, _ in edge_changes] == ["Fe", "Cu"]
    assert all(
        options["xrd"] is True
        and options["bender"] is True
        and options["tune"] is False
        and options["preserve_dcm_roll"] is True
        for _, options in edge_changes
    )

    acquisition_start = next(
        doc
        for name, doc in documents
        if name == "start" and doc["run_key"] == "default_acquire"
    )
    assert acquisition_start["BMM_agent"] == {
        "plan_name": "energy_range_alignment",
        "profile": "test-range",
        "configuration": "si111-xas",
        "elements": ["Fe", "Cu"],
        "xrd": True,
        "reference_scan_uid": "reference",
        "micrometers_per_pixel": 10.0,
    }
    descriptor_runs = {
        doc["uid"]: doc["run_start"]
        for name, doc in documents
        if name == "descriptor"
    }
    acquisition_events = [
        doc
        for name, doc in documents
        if name == "event"
        and descriptor_runs[doc["descriptor"]] == acquisition_start["uid"]
    ]
    assert len(acquisition_events) == 2

    [(trial_index, parameters, outcomes)] = agent.get_best_points()
    assert trial_index == 0
    assert parameters == {"dcm_roll": 0.1, "m2_yaw": 0.2, "m2_lateral": 0.3}
    assert outcomes["centroid_x_rmse_um"][0] == pytest.approx(10.0)
    assert outcomes["centroid_x_std_um"][0] == pytest.approx(10.0)
    assert outcomes["centroid_x_span_um"][0] == pytest.approx(20.0)
    assert outcomes["centroid_x_rmse_normalized"][0] == pytest.approx(0.2)
    assert outcomes["fwhm_x_rms_normalized"][0] == pytest.approx(1.0)
    assert outcomes["intensity_min"][0] == 1_250_000.0
    assert outcomes["intensity_mean"][0] == 1_875_000.0

def test_energy_scan_acquisition_runs_full_grid_per_suggestion():
    motor = SynAxis(name="motor")
    energy = SynAxis(name="energy")
    detector = SynSignal(
        name="detector",
        func=lambda: 10 * motor.position + energy.position,
    )
    edge_energies = {"Fe": 7112.0, "Cu": 8979.0}
    edge_motor_positions = {"Fe": -0.9, "Cu": 0.9}
    edge_changes = []

    def change_edge(element, **kwargs):
        edge_changes.append((element, kwargs))
        yield from mv(
            energy,
            edge_energies[element],
            motor,
            edge_motor_positions[element],
        )

    elements = ["Fe", "Cu"]
    acquisition_plan = make_energy_scan_acquisition_plan(
        change_edge,
        energy,
        elements,
    )
    elements[:] = ["Zn"]
    suggestions = [
        {"motor": 0.75, "_id": "right"},
        {"motor": -0.25, "_id": "left"},
    ]
    documents = []
    run_engine = RunEngine({}, call_returns_result=True)
    run_engine.subscribe(lambda name, doc: documents.append((name, doc)))

    result = run_engine(acquisition_plan(suggestions, [motor], [detector]))

    starts = [doc for name, doc in documents if name == "start"]
    assert len(starts) == 1
    [start] = starts
    assert start["run_key"] == "default_acquire"
    assert result.plan_result == start["uid"]
    assert len([doc for name, doc in documents if name == "stop"]) == 1
    descriptors = [doc for name, doc in documents if name == "descriptor"]
    assert [descriptor["name"] for descriptor in descriptors] == ["primary"]

    events = [doc for name, doc in documents if name == "event"]
    assert len(events) == 4
    routed_positions = [
        suggestion["motor"] for suggestion in start["blop_suggestions"]
    ]
    assert [element for element, _ in edge_changes] == ["Fe", "Cu"] * 2
    assert all(
        options
        == {
            "focus": True,
            "no_hslits": True,
            "mirror": False,
            "xrd": False,
            "bender": True,
            "tune": False,
            "preserve_dcm_roll": True,
        }
        for _, options in edge_changes
    )
    for index, position in enumerate(routed_positions):
        block = events[index * 2 : (index + 1) * 2]
        assert [event["data"]["motor"] for event in block] == pytest.approx(
            [position, position]
        )
        assert [event["data"]["energy"] for event in block] == [7112.0, 8979.0]
        assert [event["data"]["detector"] for event in block] == pytest.approx(
            [
                10 * position + 7112.0,
                10 * position + 8979.0,
            ]
        )


def test_energy_scan_acquisition_rejects_empty_element_list():
    energy = SynAxis(name="energy")
    edge_changes = []

    def change_edge(element, **kwargs):
        edge_changes.append((element, kwargs))
        yield from null()

    with pytest.raises(ValueError, match="Energy scan requires at least one element"):
        make_energy_scan_acquisition_plan(change_edge, energy, [])

    assert edge_changes == []


def test_energy_scan_acquisition_merges_fixed_metadata():
    motor = SynAxis(name="motor")
    energy = SynAxis(name="energy")

    def change_edge(element, **kwargs):
        yield from mv(energy, 7112.0)

    acquisition_plan = make_energy_scan_acquisition_plan(
        change_edge,
        energy,
        ["Fe"],
        metadata={
            "purpose": "fixed-purpose",
            "BMM_agent": {
                "plan_name": "energy_range_alignment",
                "profile": "energy-range-alignment",
                "configuration": "si111-xas",
                "elements": ["Fe"],
                "xrd": False,
                "reference_scan_uid": "reference",
                "micrometers_per_pixel": 10.0,
            },
        },
    )
    documents = []
    run_engine = RunEngine({}, call_returns_result=True)
    run_engine.subscribe(lambda name, doc: documents.append((name, doc)))

    run_engine(
        acquisition_plan(
            [{"motor": 0.0, "_id": "only"}],
            [motor],
            [],
            md={"purpose": "caller-purpose", "operator": "test"},
        )
    )

    start = next(doc for name, doc in documents if name == "start")
    assert start["operator"] == "test"
    assert start["purpose"] == "fixed-purpose"
    assert start["BMM_agent"] == {
        "plan_name": "energy_range_alignment",
        "profile": "energy-range-alignment",
        "configuration": "si111-xas",
        "elements": ["Fe"],
        "xrd": False,
        "reference_scan_uid": "reference",
        "micrometers_per_pixel": 10.0,
    }
    assert start["blop_suggestions"] == [{"motor": 0.0, "_id": "only"}]


def test_agent_optimization_nests_energy_scan_acquisition(
    make_profile_and_resources,
):
    profile, resources = make_profile_and_resources()
    profile = replace(
        profile,
        energy_change=replace(profile.energy_change, xrd=True, bender=False),
    )
    motor = resources.actuators["motor"]
    energy = SynAxis(name="energy")
    camera = SynSignal(
        name="camera",
        func=lambda: motor.position + energy.position,
    )
    edge_energies = {"Fe": 7112.0, "Cu": 8979.0}
    edge_motor_positions = {"Fe": -0.9, "Cu": 0.9}
    edge_changes = []

    def change_edge(element, **kwargs):
        edge_changes.append((element, kwargs))
        if kwargs["preserve_dcm_roll"]:
            yield from mv(energy, edge_energies[element])
        else:
            yield from mv(
                energy,
                edge_energies[element],
                motor,
                edge_motor_positions[element],
            )

    resources = replace(
        resources,
        sensors={"camera": camera},
        change_edge_plan=change_edge,
    )
    documents = []
    evaluation_calls = []

    def evaluate(uid, suggestions):
        evaluation_calls.append((uid, [dict(suggestion) for suggestion in suggestions]))
        return [
            {
                "_id": suggestion["_id"],
                "lateral_distance": abs(suggestion["motor"]),
                "intensity": 1_000_001.0,
            }
            for suggestion in suggestions
        ]

    acquisition_plan = make_energy_scan_acquisition_plan(
        resources.change_edge_plan,
        energy,
        ["Fe", "Cu"],
        energy_change=profile.energy_change,
    )
    agent = make_energy_alignment_agent(
        "reference",
        profile=profile,
        resources=resources,
        evaluation_function=evaluate,
        acquisition_plan=acquisition_plan,
    )
    run_engine = RunEngine({})
    run_engine.subscribe(lambda name, doc: documents.append((name, doc)))

    run_engine(agent.optimize(1))

    starts = [doc for name, doc in documents if name == "start"]
    assert [start["run_key"] for start in starts] == ["optimize", "default_acquire"]
    outer_start, inner_start = starts
    assert outer_start["uid"] != inner_start["uid"]
    stops = [doc for name, doc in documents if name == "stop"]
    assert [stop["run_start"] for stop in stops] == [
        inner_start["uid"],
        outer_start["uid"],
    ]

    assert edge_changes == [
        (
            "Fe",
            {
                "focus": True,
                "no_hslits": True,
                "mirror": False,
                "xrd": True,
                "bender": False,
                "tune": False,
                "preserve_dcm_roll": True,
            },
        ),
        (
            "Cu",
            {
                "focus": True,
                "no_hslits": True,
                "mirror": False,
                "xrd": True,
                "bender": False,
                "tune": False,
                "preserve_dcm_roll": True,
            },
        ),
    ]
    assert evaluation_calls == [
        (inner_start["uid"], inner_start["blop_suggestions"])
    ]
    [suggestion] = evaluation_calls[0][1]
    descriptor_runs = {
        doc["uid"]: doc["run_start"]
        for name, doc in documents
        if name == "descriptor"
    }
    inner_events = [
        doc
        for name, doc in documents
        if name == "event"
        and descriptor_runs[doc["descriptor"]] == inner_start["uid"]
    ]
    assert [event["data"]["motor"] for event in inner_events] == pytest.approx(
        [suggestion["motor"], suggestion["motor"]]
    )
    assert [event["data"]["energy"] for event in inner_events] == [
        7112.0,
        8979.0,
    ]
    assert [event["data"]["camera"] for event in inner_events] == pytest.approx(
        [suggestion["motor"] + 7112.0, suggestion["motor"] + 8979.0]
    )


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


def test_metadata_wrapper_injects_start_document(make_profile_and_resources):
    profile, resources = make_profile_and_resources(read_energy=lambda: 7112.5)
    documents = []

    def plan():
        yield from open_run(md={"purpose": "metadata-test"})
        yield from close_run()

    run_engine = RunEngine({})
    run_engine.subscribe(lambda name, doc: documents.append((name, doc)))
    run_engine(
        optimization_metadata_wrapper(
            plan(),
            "Fe",
            "reference",
            profile=profile,
            resources=resources,
        )
    )

    start = next(doc for name, doc in documents if name == "start")
    assert start["purpose"] == "metadata-test"
    assert start["BMM_agent"]["profile"] == "test"
    assert start["Beamline"]["energy"] == 7112.5


def test_dash_callback_builds_app(make_profile_and_resources):
    profile, resources = make_profile_and_resources()
    agent = make_energy_alignment_agent(
        "reference",
        profile=profile,
        resources=resources,
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


def test_search_reuses_reference_evaluation(
    make_profile_and_resources,
    monkeypatch,
):

    class FakeAgent:
        def optimize(self, iterations):
            yield from null()

        def get_best_points(self):
            return [(0, {"motor": 0.0}, {"centroid_distance": (0.0, 0.0)})]

    reference_image = Field(gaussian_image())
    catalog = {"reference": {"primary": {"data": {"image": reference_image}}}}

    edge_changes = []

    def change_edge(element, **kwargs):
        edge_changes.append((element, kwargs))
        yield from null()

    profile, resources = make_profile_and_resources(
        catalog=catalog,
        change_edge_plan=change_edge,
    )
    profile = replace(
        profile,
        energy_change=replace(profile.energy_change, xrd=True, bender=False),
    )
    evaluation_functions = []
    fake_agent = FakeAgent()

    def make_agent(*args, evaluation_function=None, **kwargs):
        evaluation_functions.append(evaluation_function)
        return fake_agent

    monkeypatch.setattr(
        optimization_module,
        "make_energy_alignment_agent",
        make_agent,
    )
    RunEngine({})(
        search_for_optimal_positions(
            ["Fe", "Cu"],
            "reference",
            profile=profile,
            resources=resources,
        )
    )

    assert evaluation_functions[0] is evaluation_functions[1]
    assert reference_image.read_count == 1
    assert edge_changes == [
        (
            "Fe",
            {
                "focus": True,
                "no_hslits": True,
                "mirror": False,
                "xrd": True,
                "bender": False,
            },
        ),
        (
            "Cu",
            {
                "focus": True,
                "no_hslits": True,
                "mirror": False,
                "xrd": True,
                "bender": False,
            },
        ),
    ]


def test_search_restores_prompt_after_failure():
    def failing_change_edge(*args, **kwargs):
        yield from null()
        raise RuntimeError("energy change failed")

    prompt_state = SimpleNamespace(prompt=True)
    resources = EnergyAlignmentResources(
        catalog={},
        actuators={dof.actuator: object() for dof in PER_ENERGY_ALIGNMENT.dofs},
        sensors={sensor: object() for sensor in PER_ENERGY_ALIGNMENT.sensors},
        change_edge_plan=failing_change_edge,
        prompt_state=prompt_state,
    )

    with pytest.raises(RuntimeError, match="energy change failed"):
        RunEngine({})(
            search_for_optimal_positions(
                ["Fe"],
                "reference",
                resources=resources,
            )
        )

    assert prompt_state.prompt
