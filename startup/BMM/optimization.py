from functools import partial

import numpy as np
from blop import RangeDOF, ChoiceDOF, Objective, Agent, OutcomeConstraint
from blop.plans import default_acquire
from blop.protocols import EvaluationFunction
from bluesky.utils import plan
from ax.api.protocols import IMetric
import bluesky.plans as bp
import bluesky.plan_stubs as bps
import bluesky.preprocessors as bpp


from BMM.edge import change_edge
from BMM.user_ns.dcm import dcm
from BMM.user_ns.instruments import m2, slits3
from BMM.user_ns.detectors import ic0, cam8
from BMM.functions import not_at_edge
from BMM.user_ns.bmm import BMMuser

# order is important
ENERGY_VALUES = ["Cu", "Pb", "Y", "Mo"]

# ------ DOFs ------------
# Current positions are a decent starting point
# ------------------------
dcm_roll_dof = RangeDOF(
    actuator=dcm.roll,
    bounds=(-0.365 - 10, -0.365 + 10),
    parameter_type="float",
)
m2_yaw_dof = RangeDOF(
    actuator=m2.yaw,
    bounds=(-2, 2),
    parameter_type="float",
)
m2_lateral_dof = RangeDOF(
    actuator=m2.lateral,
    bounds=(-2, 2),
    parameter_type="float",
)
energy_dof = ChoiceDOF(
    name="energy",
    values=ENERGY_VALUES,
    parameter_type="str",
    is_ordered=True,
)
dofs = [dcm_roll_dof, m2_yaw_dof, m2_lateral_dof]

# -------- Objectives ---------
lateral_distance = Objective(name="lateral_distance", minimize=True)
# intensity = Objective(name="intensity", minimize=False)

intensity = IMetric(name="intensity")

objectives = [
    lateral_distance
]

# -------- Outcome constraints ---------
distance_constraint = OutcomeConstraint("x <= 2", x=lateral_distance) # TODO verify
intensity_constraint = OutcomeConstraint("x >= 1000000", x=intensity) # TODO verify

outcome_constraints = [
    intensity_constraint
]

# --------- Custom acquisition plan --------
@plan
def scan_slits(detectors, start, stop, num_steps):
    """Scan the slit position over a range"""
    yield from bpp.stub_wrapper(bp.scan(detectors, slits3.hcenter, start, stop, num_steps))

# For slit scan
## This is a list scan over optimizer suggestions. Each suggestion, it will
## scan the energy and for each energy, scan the slit position and take an ion chamber reading per step
scan_slits_fixed = partial(scan_slits, start=-2, stop=2, num_steps=50)
scan_energy_with_slits = partial(bps.one_nd_step, take_reading=scan_slits_fixed)
acquire_energy_scan_with_slits = partial(default_acquire, per_step=scan_energy_with_slits)

def compute_stats(image):
    gray = image.squeeze().astype(np.float64)
    if gray.ndim == 3:
        gray = gray.mean(axis=-1)

    intensity = gray.sum()

    x_profile = gray.sum(axis=0)
    # [900, 1040]
    cropped_x_profile = x_profile[900:1040]

    # print(f"max y {np.argmax(gray.sum(axis=1))}, max x: {np.argmax(gray.sum(axis=0))}")

    print(f"lateral_position: {np.argmax(x_profile)}, intensity={intensity}")

# --------- Custom evaluation ---------
def beam_image_evaluation(uid: str, suggestions: list[dict]) -> list[dict]:
    """
    Evaluate a set of images per suggestion to produce
    - average lateral distance to the desired beam position
    - average intensity
    """
    # TODO: load and evaluate images
    return [
        { "_id": suggestion["_id"], "avg_lateral_distance": 1.0, "intensity": 1e4 }
        for suggestion in suggestions
    ]

class ImageEvaluation(EvaluationFunction):
    def __init__(self, tiled_client, reference_scan_uid):
        self.tiled_client = tiled_client

        ref_image = self.tiled_client[reference_scan_uid]['primary']['data']['cam-8_image'].read()
        ref_lateral_position, ref_intensity = self._compute_stats(ref_image)
        self.target_lateral_position = ref_lateral_position
        self.target_intensity = ref_intensity

    def _compute_stats(self, image: np.ndarray):
        gray = image.squeeze().astype(np.float64)
        if gray.ndim == 3:
            gray = gray.mean(axis=-1)

        # do cropping here
        ...

        # get intensity of cropped area
        intensity = gray.sum()
        
        # get max column of cropped region, this is what we consider to be the "lateral position"
        y_profile = gray.sum(axis=0) # sum along X cols

        # cropped columns
        cropped_y_profile = y_profile[900:1040]

        return np.argmax(cropped_y_profile)+900, intensity

    def __call__(self, uid: str, suggestions) -> list[dict]:
        outcomes = []
        run = self.tiled_client[uid]
        
        image = run['primary']['data']['cam-8_image'].read()

        print(f"image: {image}")

        suggestion_ids = [suggestion["_id"] for suggestion in run.metadata["start"]["blop_suggestions"]]

        for idx, sid in enumerate(suggestion_ids):
            lateral_position, intensity = self._compute_stats(image)
            lateral_distance = abs(self.target_lateral_position - lateral_position)

            outcome = {
                "_id": sid,
                "lateral_distance": lateral_distance,
                "intensity": intensity,
            }

            outcomes.append(outcome)

        return outcomes

def beam_slit_evaluation(uid: str, suggestions: list[dict]) -> list[dict]:
    """
    Evaluate a scan of slit positions per suggestion to produce
    - average lateral distance to the desired beam position - average intensity
    """
    # TODO: load and determine peak position
    return [
        { "_id": suggestion["_id"], "avg_lateral_distance": 1.0, "avg_intensity": 1e4 }
        for suggestion in suggestions
    ]


# --------- Agent setup --------
# Two separate agents based on acquisition plan and evaluation
# - `det_agent`: Acquires images to determine beam position and intensity
# - `slit_agent`: Acquires ion chamber readings at each slit position to
#                 determine beam position and intensity
#det_agent = Agent(
#    sensors=[cam4],
#    dofs=dofs,
#    objectives=objectives,
#    evaluation_function=beam_evaluation,
#    outcome_constraints=outcome_constraints
#)
#det_agent.ax_client.configure_generation_strategy(
#    initialization_budget=1,
#    initialize_with_center=False,
#)

# slit_agent = Agent(
 #   sensors=[ic0],
 #   dofs=dofs,
 #   objectives=objectives,
 #   evaluation_function=beam_slit_evaluation,
 #   acquisition_plan=acquire_energy_scan_with_slits,
 #   outcome_constraints=outcome_constraints
#)

# slit_agent.ax_client.configure_generation_strategy(
#    initialization_budget=1,
#    initialize_with_center=False,
#)

tiled_client = bmm_catalog

def search_for_optimal_positions(energies: list[str], reference_scan_uid, energy_map_filename: str = None):
    """A plan that uses Blop to search for optimal motor setpoints.

    Returns
    -------
    dict
        A lookup table mapping energy values -> motor positions.
    """
    # TODO: Save the energy_map periodically to disk
    energy_map = {}

    BMMuser.prompt = False
    for energy in energies:

        #if not_at_edge(energy, 'K'):
        yield from change_edge(energy, focus=True, no_hslits=True, mirror=False)
        agent = Agent(
                sensors=[cam8],
                dofs=dofs,
                objectives=objectives,
                evaluation_function=ImageEvaluation(tiled_client, reference_scan_uid=reference_scan_uid),
                outcome_constraints=outcome_constraints
                )
        agent.ax_client.configure_generation_strategy(initialization_budget=1, initialize_with_center=False)

        yield from agent.optimize(20)

        best_points = agent.get_best_points()

        print(f"best point for {energy} is {best_points}")
        energy_map[energy] = best_points

        if energy_map_filename:
            with open(energy_map_filename, "wb") as f:
                pickle.dump(energy_map, f)


    print(f"{energy_map=}")
    BMMuser.prompt = True
    return energy_map

