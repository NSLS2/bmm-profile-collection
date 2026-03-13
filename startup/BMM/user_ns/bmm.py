
import logging
logger = logging.getLogger('ophyd')
logger.setLevel('INFO')
logger = logging.getLogger('bluesky')
logger.setLevel('WARNING')

import redis
from BMM.user_ns.base import startup_dir, profile_configuration
from BMM.user_ns.kafka import kafka
from BMM.workspace import initialize_workspace, rkvs, rkvs_keys
initialize_workspace()
kafka.rkvs = rkvs

import json, time, os

## suppress the thing where matplotlib raises a new plot window to the top, stealing focus
import matplotlib as mpl
mpl.rcParams['figure.raise_window'] = False

#DATA = os.path.join(os.getenv('HOME'), 'Data', 'bucket') + '/'
BMM_CONFIGURATION_LOCATION = os.path.join(startup_dir, 'lookup_table')

from bmm_tools.tools.messages import *  # error_msg et al. + boxedtext
from BMM.functions            import run_report, elapsed_time

run_report(__file__, text='functions and other basics')
run_report('\t'+'logging')
from BMM.logging import report, BMM_log_info, BMM_msg_hook#, BMMbot

from bmm_tools.tools.misc   import now
from bmm_tools.slack.bmmbot import BMMbot

from bluesky.preprocessors   import finalize_wrapper

run_report('\t'+'user')
from BMM.user import BMM_User

try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False



run_report('\t'+'recovering user configuration')
BMMuser = BMM_User()
BMMuser.start_experiment_from_serialization()
run_report('\t'+'configuring Slack bmmbot')
BMMuser.bmmbot = BMMbot()

if is_re_worker_active:
    BMMuser.bmmbot._bmmbot_secret = profile_configuration.get('slack_qs', 'bmmbot_secret')
else:
    BMMuser.bmmbot._bmmbot_secret = profile_configuration.get('slack', 'bmmbot_secret')
BMMuser.bmmbot._redis_client = redis.Redis(host=profile_configuration.get('services', 'nsls2_redis'))
BMMuser.bmmbot._pass_api = profile_configuration.get('services', 'pass_api') + "/{pass_id}/slack-channels"
BMMuser.bmmbot.refresh_channel()
kafka.workspace = BMMuser.workspace


if BMMuser.pds_mode is None:
    try:                        # do the right thing when "%run -i"-ed
        BMMuser.pds_mode = get_mode()
    except:                     # else wait until later to set this correctly, get_mode()
        pass
## some backwards compatibility....
whoami           = BMMuser.show_experiment
begin_experiment = BMMuser.begin_experiment
end_experiment   = BMMuser.end_experiment


import atexit, os

def teardown():
    print("Shutting down: ", end=' ')
    BMMuser.state_to_redis(filename=os.path.join(BMMuser.workspace, '.BMMuser'), prefix='')
    kafka.producer.flush()
    
atexit.register(teardown)




# RE.msg_hook = BMM_msg_hook
