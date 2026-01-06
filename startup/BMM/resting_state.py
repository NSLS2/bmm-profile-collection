try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False

from bluesky.plan_stubs import mv, sleep
import datetime
import matplotlib

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from bmm_tools.tools.misc   import now

from BMM.logging        import BMM_msg_hook
#from BMM.suspenders     import BMM_suspenders, BMM_clear_suspenders
from BMM.workspace      import rkvs

from BMM.user_ns.base        import profile_configuration
from BMM.user_ns.bmm         import BMMuser, kafka
from BMM.user_ns.dcm         import dcm
from BMM.user_ns.dwelltime   import _locked_dwell_time, with_quadem, with_iy, with_pips, with_pilatus
from BMM.user_ns.detectors   import quadem1, ION_CHAMBERS, pilatus
from BMM.user_ns.instruments import xafs_wheel
from BMM.user_ns.dcm         import *

def resting_redis():
    user_ns['rkvs'].set('BMM:scan:type', 'idle')
    user_ns['rkvs'].set('BMM:scan:starttime', datetime.datetime.timestamp(datetime.datetime.now()))
    user_ns['rkvs'].set('BMM:scan:estimated', 0)
    return


mapping = {'quadem': user_ns['quadem1'],
           'ic0': user_ns['ic0'],
           'ic1': user_ns['ic1'],
           'ic2': user_ns['ic2'],
           'xspress3': user_ns['xs'],
           'pilatus': user_ns['pilatus'],
           'eiger': user_ns['eiger'],
           'dante': user_ns['dante'],}
           # Deprecated detectors: struck, dualem

## this does not work because xs does not report as connected ...
def check_dwell_time():
    is_ok = True
    for det in mapping.keys():
        if f'{det}_dwell_time' in _locked_dwell_time.read_attrs:
            if mapping[det].connected is not True:
                print(f'{det} is not connected')
                is_ok = False
    return is_ok

def resting_state():
    '''
    Command line tool to bring controls into their resting state:

    - quadEM enabled and measuring
    - dwell time set to 1/2 second
    - electron yield channel (quadEM channel 4) hinted as 'omitted'
    - all electrometers set to continuous mode and acquiring
    - user prompt set to True (False is using QS). macro dry-run set to False, RE.msg_hook set to BMM_msg_hook
    - restaing state values set in redis
    _ kafka sent resting state message
    '''
    BMMuser.prompt, BMMuser.macro_dryrun, BMMuser.instrument = True, False, ''
    
    if with_quadem is True:
        if with_iy is True or with_pips is True:
            quadem1.Iy.kind = 'hinted'
        else:
            quadem1.Iy.kind = 'omitted'
    # if with_pilatus is True:
    #     pilatus.stats.kind = 'hinted'
    # else:
    #     #pilatus.stats.kind = 'omitted'
    #     pass
    ## NEVER prompt when using queue server
    if is_re_worker_active() is True:
        BMMuser.prompt = False
    if with_quadem is True:
        quadem1.on(quiet=True)
    for electrometer in ION_CHAMBERS:
        electrometer.acquire.put(1)
        electrometer.acquire_mode.put(0)
    dcm.kill()
    dcm.bragg.clear_encoder_loss()
    dcm.mode = 'fixed'
    user_ns['m2_bender'].kill()
    # if 'ga' in user_ns:
    #     user_ns['ga'].alloff()
    kafka.message({'resting_state': True,})
    #user_ns['RE'].msg_hook = BMM_msg_hook
    ##if is_re_worker_active() is False:
    ##    matplotlib.use('Qt5Agg')
    resting_redis()
    if profile_configuration.getboolean('sdd', 'xspress3') is True:
        xs1 = user_ns['xs1']
        xs1.channel08.get_mcaroi(mcaroi_number=16).kind = 'hinted'
        xs1.channel08.get_mcaroi(mcaroi_number=16).total_rbv.kind = 'hinted'
    _locked_dwell_time.move(0.5)
    
def resting_state_plan():
    '''
    Plan for bringing controls into their resting state:

    - dwell time set to 1/2 second
    - electron yield channel (quadEM channel 4) hinted as 'omitted'
    - RE.msg_hook set to BMM_msg_hook
    '''

    #BMMuser.prompt = True
    #BMMuser.prompt, BMMuser.macro_dryrun, BMMuser.instrument , quadem1.Iy.kind = True, False, '', 'omitted'
    #yield from quadem1.on_plan()
    if with_quadem is True:
        if with_iy is True or with_pips is True:
            quadem1.Iy.kind = 'hinted'
        else:
            quadem1.Iy.kind = 'omitted'
    # if with_pilatus is True:
    #     pilatus.stats.kind = 'hinted'
    # else:
    #     #pilatus.stats.kind = 'omitted'
    #     pass
    #BMMuser.instrument = ''
    yield from mv(_locked_dwell_time, 0.5)
    for electrometer in ION_CHAMBERS:
        yield from mv(electrometer.acquire, 1)
        yield from mv(electrometer.acquire_mode, 0)
    #yield from mv(user_ns['dm3_bct'].kill_cmd, 1)
    yield from sleep(0.2)
    # if 'ga' in user_ns:
    #     yield from user_ns['ga'].alloff_plan()
    yield from dcm.kill_plan()
    yield from mv(dcm.bragg.clear_enc_lss, 1)
    user_ns['m2_bender'].kill()
    dcm.mode = 'fixed'
    kafka.message({'resting_state': True,})
    #user_ns['RE'].msg_hook = BMM_msg_hook
    ##if is_re_worker_active() is False:
    ##    matplotlib.use('Qt5Agg')
    resting_redis()
    if profile_configuration.getboolean('sdd', 'xspress3') is True:
        xs1 = user_ns['xs1']
        xs1.channel08.get_mcaroi(mcaroi_number=16).kind = 'hinted'
        xs1.channel08.get_mcaroi(mcaroi_number=16).total_rbv.kind = 'hinted'
    

def end_of_macro():
    '''Plan for bringing controls into their resting state at the end of
    a macro or when a macro is stopped or aborted:

    - quadEM and Struck scaler enabled and measuring
    - dwell time set to 1/2 second
    - electron yield channel (quadEM channel 4) hinted as 'omitted'
    - user prompt set to True. macro dry-run set to False, RE.msg_hook set to BMM_msg_hook
    '''
    
    BMMuser.prompt, BMMuser.macro_dryrun, BMMuser.instrument = True, False, ''
    if with_quadem is True:
        if with_iy is True or with_pips is True:
            quadem1.Iy.kind = 'hinted'
        else:
            quadem1.Iy.kind = 'omitted'
    # if with_pilatus is True:
    #     pilatus.stats.kind = 'hinted'
    # else:
    #     #pilatus.stats.kind = 'omitted'
    #     pass
    ## NEVER prompt when using queue server
    if is_re_worker_active() is True:
        BMMuser.prompt = False
    BMMuser.running_macro, BMMuser.lims = False, True
    if with_quadem is True:
        yield from quadem1.on_plan()
    yield from mv(_locked_dwell_time, 0.5)
    #yield from mv(user_ns['dm3_bct'].kill_cmd, 1)
    yield from sleep(0.2)
    # if 'ga' in user_ns:
    #     yield from user_ns['ga'].alloff_plan()
    yield from dcm.kill_plan()
    yield from mv(dcm.bragg.clear_enc_lss, 1)
    user_ns['m2_bender'].kill()
    yield from xafs_wheel.recenter()
    dcm.mode = 'fixed'
    user_ns['RE'].msg_hook = BMM_msg_hook
    #if is_re_worker_active() is False:
    #    matplotlib.use('Qt5Agg')
    resting_redis()
    suspenders.clear_suspenders()

