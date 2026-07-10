import numpy as np
from blop import RangeDOF, Objective, Agent, OutcomeConstraint
from blop.protocols import EvaluationFunction
from ax.api.protocols import IMetric
from tiled.client.container import Container
import pickle


from BMM.edge import change_edge
from BMM.user_ns.dcm import dcm
from BMM.user_ns.instruments import m2
from BMM.user_ns.detectors import ic0, cam8
from BMM.functions import not_at_edge # we may need this again
from BMM.user_ns.bmm import BMMuser

tiled_client = bmm_catalog

# ------ DOFs ------------
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
dofs = [dcm_roll_dof, m2_yaw_dof, m2_lateral_dof]

# -------- Objectives ---------
lateral_distance_obj = Objective(name="lateral_distance", minimize=True)

# we can make intensity an objective if we are getting insufficient optimal beam
intensity_obj = Objective(name="intensity", minimize=False)
intensity_metric = IMetric(name="intensity")

objectives = [
    lateral_distance_obj
]

# -------- Outcome constraints ---------
distance_constraint = OutcomeConstraint("x <= 2", x=lateral_distance_obj) # TODO verify this again
intensity_constraint = OutcomeConstraint("x >= 1000000", x=intensity_metric) # TODO verify this again

outcome_constraints = [
    intensity_constraint
]

# use this function in bsui for sanity checking
def compute_stats(uid: str):
    image = tiled_client[uid]['primary']['data']['cam8-image'].read()
    intensity_ic0 = tiled_client[uid]['primary']['I0'].read()

    gray = image.squeeze().astype(np.float64)
    if gray.ndim == 3:
        gray = gray.mean(axis=-1)

    crop_region_x = [900, 1040]

    # do cropping here (900, 1040) TODO confirm this again
    cropped_y_profile = gray.sum(axis=0)[crop_region_x[0]:crop_region_x[1]]

    # get the maximum column position (lateral position)
    lateral_position = np.argmax(cropped_y_profile) + crop_region_x[0]

    # get intensity of cropped area
    cropped_intensity = cropped_y_profile.sum()

    print(f"lateral_position: {lateral_position}, cropped_intensity={cropped_intensity}, ic0_intensity={intensity_ic0}")

# --------- Custom evaluation ---------
class ImageEvaluation(EvaluationFunction):
    def __init__(self, tiled_client: Container, reference_scan_uid: str):
        self.tiled_client = tiled_client

        # take an image before starting optimizations, use this as our "baseline" position/intensity
        ref_image = self.tiled_client[reference_scan_uid]['primary']['data']['cam-8_image'].read()
        ref_lateral_position, ref_intensity = self._compute_stats(ref_image)
        self.target_lateral_position = ref_lateral_position
        self.target_intensity = ref_intensity

    def _compute_stats(self, image: np.ndarray) -> tuple[float, float]:
        gray = image.squeeze().astype(np.float64)
        if gray.ndim == 3:
            gray = gray.mean(axis=-1)
        
        crop_region_x = [900, 1040]

        # do cropping here (900, 1040) TODO confirm this again
        cropped_y_profile = gray.sum(axis=0)[crop_region_x[0]:crop_region_x[1]]

        # get the maximum column position (lateral position)
        lateral_position = np.argmax(cropped_y_profile) + crop_region_x[0]

        # get intensity of cropped area
        cropped_intensity = cropped_y_profile.sum()
        
        return lateral_position, cropped_intensity

    def __call__(self, uid: str, suggestions) -> list[dict]:
        outcomes = []
        run = self.tiled_client[uid]
        
        image = run['primary']['data']['cam-8_image'].read()
        intensity_ic0 = run['primary']['I0'].read()

        suggestion_ids = [suggestion["_id"] for suggestion in run.metadata["start"]["blop_suggestions"]]

        for idx, sid in enumerate(suggestion_ids):
            lateral_position, intensity = self._compute_stats(image, intensity_ic0)
            lateral_distance = abs(self.target_lateral_position - lateral_position)

            outcome = {
                "_id": sid,
                "lateral_distance": lateral_distance,
                "intensity": intensity,
            }

            outcomes.append(outcome)

        return outcomes

def search_for_optimal_positions(energies: list[str], reference_scan_uid: str, energy_map_filename: str = None):
    """A plan that uses Blop to search for optimal motor setpoints.

    Returns
    -------
    dict
        A lookup table mapping energy values -> motor positions.
    """
    energy_map = {}
    BMMuser.prompt = False

    for energy in energies:
        # change energy
        yield from change_edge(energy, focus=True, no_hslits=True, mirror=False)

        # initialize agent
        agent = Agent(
                    sensors=[cam8, ic0],
                    dofs=dofs,
                    objectives=objectives,
                    evaluation_function=ImageEvaluation(tiled_client, reference_scan_uid=reference_scan_uid),
                    outcome_constraints=outcome_constraints
                )

        # TODO is this the generation strategy we want?
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
