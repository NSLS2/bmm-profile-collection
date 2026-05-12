from functools import partial

from blop import RangeDOF, ChoiceDOF, Objective, Agent, OutcomeConstraint
from blop.plans import default_acquire
from bluesky.utils import plan
import bluesky.plans as bp
import bluesky.plan_stubs as bps
import bluesky.preprocessors as bpp

from BMM.edge import change_edge
from BMM.user_ns.dcm import dcm
from BMM.user_ns.instruments import m2, slits3
from BMM.user_ns.detectors import ic0, cam8


# order is important
ENERGY_VALUES = ["Cu", "Pb", "Y", "Mo"]

# ------ DOFs ------------
# Current positions are a decent starting point
# ------------------------
dcm_roll_dof = RangeDOF(
    actuator=dcm.roll,
    bounds=(-0.365 - 0.5, -0.365 + 0.5),
    parameter_type="float",
)
m2_yaw_dof = RangeDOF(
    actuator=m2.yaw,
    bounds=(-0.1, 0.1),
    parameter_type="float",
)
m2_lateral_dof = RangeDOF(
    actuator=m2.lateral,
    bounds=(-0.2, 0.2),
    parameter_type="float",
)
energy_dof = ChoiceDOF(
    name="energy",
    values=ENERGY_VALUES,
    parameter_type="str",
    is_ordered=True,
)
dofs = [dcm_roll_dof, m2_yaw_dof, m2_lateral_dof, energy_dof]

# -------- Objectives ---------
avg_lateral_distance_obj = Objective(name="avg_lateral_distance", minimize=True)
avg_intensity_obj = Objective(name="avg_intensity", minimize=False)

objectives = [
    avg_lateral_distance_obj,
    avg_intensity_obj,
]

# -------- Outcome constraints ---------
distance_constraint = OutcomeConstraint("x <= 50", x=avg_lateral_distance_obj) # verify
intensity_constraint = OutcomeConstraint("x >= 1000000", x=avg_intensity_obj) # verify

outcome_constraints = [
    distance_constraint,
    intensity_constraint,
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

# --------- Custom evaluation ---------
def beam_image_evaluation(uid: str, suggestions: list[dict]) -> list[dict]:
    """
    Evaluate a set of images per suggestion to produce
    - average lateral distance to the desired beam position
    - average intensity
    """
    # TODO: load and evaluate images
    return [
        { "_id": suggestion["_id"], "avg_lateral_distance": 1.0, "avg_intensity": 1e4 }
        for suggestion in suggestions
    ]


def beam_slit_evaluation(uid: str, suggestions: list[dict]) -> list[dict]:
    """
    Evaluate a scan of slit positions per suggestion to produce
    - average lateral distance to the desired beam position
    - average intensity
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

slit_agent = Agent(
    sensors=[ic0],
    dofs=dofs,
    objectives=objectives,
    evaluation_function=beam_slit_evaluation,
    acquisition_plan=acquire_energy_scan_with_slits,
    outcome_constraints=outcome_constraints
)
slit_agent.ax_client.configure_generation_strategy(
    initialization_budget=1,
    initialize_with_center=False,
)

def search_for_optimal_positions(agent):
    """A plan that uses Blop to search for optimal motor setpoints.

    Returns
    -------
    dict
        A lookup table mapping energy values -> motor positions.
    """
    # TODO: Save the energy_map periodically to disk
    energy_map = {}
    for energy in ENERGY_VALUES:
        #yield from change_edge(energy)  # TODO: Uncomment with real beam
        agent._optimizer.fixed_parameters = {energy_dof.parameter_name: energy}
        yield from agent.optimize(2)  # TODO: Increase as needed
        best_points = agent.get_best_points()
        energy_map[energy] = best_points

    print(f"{energy_map=}")
    return energy_map

