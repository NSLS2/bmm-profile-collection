## copied from /nsls2/auto-storage/cms/legacy/xf11bm/data/2025_2/KChen-Wiegart/qserver_comm.py

from bluesky_queueserver_api import BPlan
from bluesky_queueserver_api.http import REManagerAPI


# server URL and port edited by BR 6/16/26
qs = REManagerAPI(http_server_uri="https://xf06bm-bmm-qs1.nsls2.bnl.gov:443")

## need a new key for the new server  -BR, 6/16/26
with open("/nsls2/data/cms/shared/config/agent_runtime/qserver_http_api_keys/BMM_key_20250620", "r") as f:
    api_key = f.read().strip()
qs.set_authorization_key(api_key=api_key)
qs.ping()


def single_plan_per(composition, distance, time, priority: Literal["front", "back"]):
    """Adds one single plan to the queue at BMM.
    The plan should have the form:
       def plan(composition, distance, time)
    
    Optional fourth argument is either 'XANES' or 'EXAFS'

    The plan at BMM will interpret the results and perform the associated tasks.

    CMS's responsibility is only to specify those three parameters.

    The BMM plan will digest these conceptual variables and convert
    them into motor positions and edges, and mono adjustments.

    See agent_plans.py

    """
    plan = BPlan('CMS_driven_measurement', composition, distance, time)
    qs.item_add(plan, pos=priority)
