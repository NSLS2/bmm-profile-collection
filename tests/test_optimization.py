from dataclasses import replace
from types import SimpleNamespace
import unittest

from blop import RangeDOF
from bluesky import RunEngine
from bluesky.plan_stubs import null
import numpy as np
from ophyd.sim import SynAxis, SynSignal

from BMM.optimization import (
    CAMERA_CENTERING,
    BeamEvaluationConfig,
    EnergyAlignmentResources,
    ImageEvaluation,
    compute_image_stats,
    make_energy_alignment_agent,
    search_for_optimal_positions,
)


class Field:
    def __init__(self, value):
        self.value = value

    def read(self):
        return self.value


def run_with_fields(**fields):
    return {
        "primary": {
            "data": {name: Field(value) for name, value in fields.items()}
        }
    }


class EnergyAlignmentTests(unittest.TestCase):
    def test_compute_image_stats_uses_configured_crop(self):
        parameters = BeamEvaluationConfig(
            image_field="image",
            intensity_field="i0",
            crop_region=(2, 5),
        )
        image = np.zeros((3, 7))
        image[:, 1] = 100
        image[:, 4] = 2

        stats = compute_image_stats(image, parameters)

        self.assertEqual(stats.lateral_position, 4)
        self.assertEqual(stats.cropped_intensity, 6)

    def test_image_evaluation_returns_one_outcome_per_suggestion(self):
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

        self.assertEqual(
            outcomes,
            [
                {"_id": "first", "lateral_distance": 1, "intensity": 4.0},
                {"_id": "second", "lateral_distance": 1, "intensity": 6.0},
            ],
        )

    def test_agent_factory_returns_fresh_agents(self):
        parameters = BeamEvaluationConfig(
            image_field="image",
            intensity_field="i0",
            crop_region=(0, 2),
        )
        profile = replace(
            CAMERA_CENTERING,
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
            optimization=replace(CAMERA_CENTERING.optimization, iterations=2),
        )
        resources = EnergyAlignmentResources(
            catalog={"reference": run_with_fields(image=np.ones((1, 2, 2)))},
            actuators={"motor": SynAxis(name="motor")},
            sensors={"camera": SynSignal(name="camera", func=lambda: 1)},
            change_edge_plan=lambda *args, **kwargs: null(),
            prompt_state=SimpleNamespace(prompt=True),
        )

        first = make_energy_alignment_agent(
            "reference", profile=profile, resources=resources
        )
        second = make_energy_alignment_agent(
            "reference", profile=profile, resources=resources
        )

        self.assertIsNot(first, second)
        self.assertEqual(profile.dofs[0].actuator, "motor")
        self.assertEqual(profile.dofs[0].step_size, 0.1)

    def test_search_restores_prompt_after_failure(self):
        def failing_change_edge(*args, **kwargs):
            yield from null()
            raise RuntimeError("energy change failed")

        prompt_state = SimpleNamespace(prompt=True)
        resources = EnergyAlignmentResources(
            catalog={},
            actuators={dof.actuator: object() for dof in CAMERA_CENTERING.dofs},
            sensors={sensor: object() for sensor in CAMERA_CENTERING.sensors},
            change_edge_plan=failing_change_edge,
            prompt_state=prompt_state,
        )

        with self.assertRaisesRegex(RuntimeError, "energy change failed"):
            RunEngine({})(
                search_for_optimal_positions(
                    ["Fe"],
                    "reference",
                    resources=resources,
                )
            )

        self.assertTrue(prompt_state.prompt)


if __name__ == "__main__":
    unittest.main()

