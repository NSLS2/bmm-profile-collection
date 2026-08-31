from dataclasses import replace
import pickle
from types import SimpleNamespace

from blop import Objective, RangeDOF
from bluesky import RunEngine
from bluesky.plan_stubs import close_run, null, open_run
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
    _write_energy_map,
    compute_image_stats,
    get_energy_alignment_profile,
    make_energy_alignment_agent,
    make_energy_scan_acquisition_plan,
    optimization_metadata_wrapper,
    search_for_optimal_positions,
)


class Field:
    def __init__(self, value):
        self.value = value

    def read(self):
        return self.value


def run_with_fields(**fields):
    return {"primary": {"data": {name: Field(value) for name, value in fields.items()}}}


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
            crop_region=(0, 2),
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
                {"reference": run_with_fields(image=np.ones((1, 2, 2)))}
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


def test_compute_image_stats_uses_configured_crop():
    parameters = BeamEvaluationConfig(
        image_field="image",
        intensity_field="i0",
        crop_region=(2, 5),
    )
    image = np.zeros((3, 7))
    image[:, 1] = 100
    image[:, 4] = 2

    stats = compute_image_stats(image, parameters)

    assert stats.lateral_position == 4
    assert stats.cropped_intensity == 6


def test_compute_image_stats_rejects_frame_stack():
    parameters = BeamEvaluationConfig(
        image_field="image",
        intensity_field="i0",
        crop_region=(0, 5),
    )

    with pytest.raises(ValueError, match="ambiguous 3-D shape"):
        compute_image_stats(np.ones((2, 3, 5)), parameters)


def test_profile_rejects_unsupported_evaluation_outcome():
    profile = replace(
        PER_ENERGY_ALIGNMENT,
        name="unsupported-outcome",
        objectives=(Objective(name="beam_width", minimize=True),),
    )

    with pytest.raises(ValueError, match="beam_width"):
        get_energy_alignment_profile(profile)


def test_image_evaluation_returns_one_outcome_per_suggestion():
    parameters = BeamEvaluationConfig(
        image_field="image",
        intensity_field="i0",
        crop_region=(1, 5),
    )
    reference = np.zeros((1, 2, 6))
    reference[0, :, 2] = 5
    acquired = np.zeros((2, 2, 6))
    acquired[0, :, 3] = 2
    acquired[1, :, 1] = 3
    catalog = {
        "reference": run_with_fields(image=reference),
        "acquired": run_with_fields(image=acquired),
    }
    evaluator = ImageEvaluation(catalog, "reference", parameters)

    outcomes = evaluator(
        "acquired",
        [{"_id": "first"}, {"_id": "second"}],
    )

    assert outcomes == [
        {"_id": "first", "lateral_distance": 1, "intensity": 4.0},
        {"_id": "second", "lateral_distance": 1, "intensity": 6.0},
    ]


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


def test_energy_scan_acquisition_runs_full_grid_per_suggestion():
    motor = SynAxis(name="motor")
    energy = SynAxis(name="energy")
    detector = SynSignal(
        name="detector",
        func=lambda: 10 * motor.position + energy.position,
    )
    energy_points = [100.0, 101.0]
    acquisition_plan = make_energy_scan_acquisition_plan(energy, energy_points)
    energy_points[:] = [999.0]
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
    for index, position in enumerate(routed_positions):
        block = events[index * 2 : (index + 1) * 2]
        assert [event["data"]["motor"] for event in block] == [position, position]
        assert [event["data"]["energy"] for event in block] == [100.0, 101.0]
        assert [event["data"]["detector"] for event in block] == [
            10 * position + 100.0,
            10 * position + 101.0,
        ]


def test_energy_scan_acquisition_rejects_empty_grid():
    energy = SynAxis(name="energy")

    with pytest.raises(ValueError, match="Energy scan requires at least one energy"):
        make_energy_scan_acquisition_plan(energy, [])


def test_agent_optimization_nests_energy_scan_acquisition(
    make_profile_and_resources,
):
    profile, resources = make_profile_and_resources()
    motor = resources.actuators["motor"]
    energy = SynAxis(name="energy")
    camera = SynSignal(
        name="camera",
        func=lambda: motor.position + energy.position,
    )
    resources = replace(resources, sensors={"camera": camera})
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

    acquisition_plan = make_energy_scan_acquisition_plan(energy, [100.0, 101.0])
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
    assert [event["data"]["motor"] for event in inner_events] == [
        suggestion["motor"],
        suggestion["motor"],
    ]
    assert [event["data"]["energy"] for event in inner_events] == [100.0, 101.0]
    assert [event["data"]["camera"] for event in inner_events] == pytest.approx(
        [suggestion["motor"] + 100.0, suggestion["motor"] + 101.0]
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
    assert agent_metadata["objectives"] == ["lateral_distance"]


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
    figure = callback.compute_figure("motor", "motor", "lateral_distance")
    app = callback.build_app()

    assert callback.dof_names == ["motor"]
    assert callback.objective_names == ["lateral_distance"]
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
    class CountingField(Field):
        def __init__(self, value):
            super().__init__(value)
            self.read_count = 0

        def read(self):
            self.read_count += 1
            return super().read()

    class FakeAgent:
        def optimize(self, iterations):
            yield from null()

        def get_best_points(self):
            return [(0, {"motor": 0.0}, {"lateral_distance": (0.0, 0.0)})]

    reference_image = CountingField(np.ones((1, 2, 2)))
    catalog = {"reference": {"primary": {"data": {"image": reference_image}}}}

    def change_edge(*args, **kwargs):
        yield from null()

    profile, resources = make_profile_and_resources(
        catalog=catalog,
        change_edge_plan=change_edge,
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
