from functools import partial

from blop import RangeDOF, Objective, Agent, OutcomeConstraint
from blop.plans import default_acquire
from bluesky.utils import plan
import bluesky.plans as bp
import bluesky.plan_stubs as bps

from BMM.edge import change_edge
from BMM.user_ns.dcm import dcm
from BMM.user_ns.instruments import m2, slits3
from BMM.user_ns.detectors import ic0, cam8


# ------ DOFs ------------
# Current positions are a decent starting point
# ------------------------
dcm_roll_dof = RangeDOF(
    actuator=dcm.roll,
    bounds=(), # TODO:
    parameter_type="float",
)
m2_yaw_dof = RangeDOF(
    actuator=m2.yaw,
    bounds=(), # TODO:
    parameter_type="float",
)
m2_lateral_dof = RangeDOF(
    actuator=m2.lateral,
    bounds=(), # TODO:
    parameter_type="float",
)
dofs = [dcm_roll_dof, m2_yaw_dof, m2_lateral_dof]

# -------- Objectives ---------
avg_lateral_distance_obj = Objective(name="avg_lateral_distance", minimize=True)
avg_intensity_obj = Objective(name="avg_intensity", minimize=False)

objectives = [
    avg_lateral_distance_obj,
    avg_intensity_obj,
]

# -------- Outcome constraints ---------
distance_constraint = OutcomeConstraint("x <= 50", x=avg_lateral_distance_obj) # verify
intensity_constraint = OutcomeConstraint("x > 1e6", x=avg_intensity_obj) # verify

outcome_constraints = [
    distance_constraint,
    intensity_constraint,
]

# --------- Custom acquisition plan --------
@plan
def scan_slits(detectors, start, stop, num_steps):
    """Scan the slit position over a range"""
    yield from bp.scan(detectors, slits3.hcenter, start, stop, num_steps)


@plan
def scan_energy_per_step(detectors, step, pos_cache, take_reading=None):
    """
    After moving to a new position, we do an energy scan to take a reading
    at each step of the energy.
    """

    if take_reading is None:
        take_reading = bps.trigger_and_read

    # scan energy
    #   trigger detectors or scan slits
    for energy in ["Cu", "Pb", "Y", "Mo"]:
        yield from change_edge(energy)
        yield from take_reading(list(detectors) + list(step.keys()))


# For imaging detector
## This is a list scan over optimizer suggestions. Each suggestion, it will
## scan the energy and trigger the sensors once per energy
acquire_energy_scan_with_images = partial(default_acquire, per_step=scan_energy_per_step)

# For slit scan
## This is a list scan over optimizer suggestions. Each suggestion, it will
## scan the energy and for each energy, scan the slit position and take an ion chamber reading per step
scan_slits_fixed = partial(scan_slits, start=0, stop=200, num_steps=50)
scan_energy_with_slits = partial(scan_energy_per_step, take_reading=scan_slits_fixed)
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
        { "_id": suggestions["_id"], "avg_lateral_distance": 1.0, "avg_intensity": 1e4 }
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
        { "_id": suggestions["_id"], "avg_lateral_distance": 1.0, "avg_intensity": 1e4 }
        for suggestion in suggestions
    ]


# --------- Agent setup --------
# Two separate agents based on acquisition plan and evaluation
# - `det_agent`: Acquires images to determine beam position and intensity
# - `slit_agent`: Acquires ion chamber readings at each slit position to
#                 determine beam position and intensity
det_agent = Agent(
    sensors=[cam8], # TODO: Add cam7 here
    dofs=dofs,
    objectives=objectives,
    evaluation_function=beam_evaluation,
    acquisition_plan=acquire_with_energy_scan_with_images,
    outcome_constraints=outcome_constraints
)

slit_agent = Agent(
    sensors=[ic0], # TODO: Add ion chamber signal to read
    dofs=dofs,
    objectives=objectives,
    evaluation_function=beam_slit_evaluation,
    acquisition_plan=acquire_energy_scan_with_slits,
    outcome_constraints=outcome_constraints
)

