from bluesky.suspenders import SuspendFloor, SuspendBoolHigh, SuspendBoolLow

import uuid

from BMMCommon.tools.messages import bold_msg, error_msg, warning_msg, whisper
from BMMCommon.tools.animated_prompt import PROMPTNC, animated_prompt

from BMM.user_ns.detectors   import quadem1
from BMM.user_ns.metadata    import ring
from BMM.user_ns.instruments import bmps, idps
from BMM.functions import PROMPT
from BMM.user_ns.bmm        import kafka

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)


#RE.clear_suspenders()
all_BMM_suspenders = list()

## ----------------------------------------------------------------------------------
## suspend when I0 drops below 0.1 nA (not in use)
suspender_I0 = SuspendFloor(quadem1.I0, 0.1, resume_thresh=1, sleep=5)
#all_BMM_suspenders.append(suspender_I0)


def beamdown_message():

    ## ----------------------------------------------------------------------------------
    ## suspend upon beam dump, resume 30 seconds after hitting 90% of fill target
    warning_msg('''
*************************************************************

  The beam has dumped. :(

  You do not need to do anything.  Bluesky suspenders have
  noticed the loss of beam and have paused your scan.

  Your scan will resume soon after the beam returns.
''')
    whisper('''
      You may also terminate your scan by hitting 
      C-c twice then entering RE.stop()
''')
    warning_msg('''                                  
*************************************************************
''')
    #print(beam_dump_screen_message)
    kafka.message({'echoslack': True, 'text': ':skull_and_crossbones: Beam has dumped! :skull_and_crossbones:'})
    #post_to_slack(':skull_and_crossbones: Beam has dumped! :skull_and_crossbones:')
    yield from null()
def beamup_message():
    kafka.message({'echoslack': True, 'text': ':sunrise: Beam has returned! :sunrise:'})
    #post_to_slack(':sunrise: Beam has returned! :sunrise:')
    yield from null()

try:
    if ring.filltarget.connected is True and ring.filltarget.get() > 20:
        suspender_ring_current = SuspendFloor(ring.current, 10, resume_thresh=0.9 * ring.filltarget.get(),
                                              sleep=60,
                                              pre_plan=beamdown_message,
                                              post_plan=beamup_message)
        all_BMM_suspenders.append(suspender_ring_current)
except Exception as e:
    raise ValueError(f'failed to create ring current suspender: {e}')
    pass

## ----------------------------------------------------------------------------------
## suspend if the BM photon shutter closes, resume 5 seconds after opening
try:
    suspender_bmps = SuspendBoolLow(bmps.state, sleep=60)
    all_BMM_suspenders.append(suspender_bmps)
except Exception as e:
    error_msg(f'failed to create bpms suspender: {e}')
    pass

    
## ----------------------------------------------------------------------------------
## suspend if the main photon shutter closes, resume 5 seconds after opening
try:
    suspender_sha = SuspendBoolLow(idps.state, sleep=60)
    all_BMM_suspenders.append(suspender_sha)
except Exception as e:
    error_msg(f'failed to create sha suspender: {e}')
    pass

## ----------------------------------------------------------------------------------
## suspend if the experimental photon shutter closes, resume 5 seconds after opening
from bluesky.plan_stubs import null
#from BMM.logging import post_to_slack


def tell_slack_shb_closed():
    print(beam_dump_screen_message)
    kafka.message({'echoslack': True, 'text': 'B shutter closed'})
    #post_to_slack('B shutter closed')
    yield from null()
def tell_slack_shb_opened():
    #print('triggering opened message')
    kafka.message({'echoslack': True, 'text': 'B shutter opened'})
    #post_to_slack('B shutter opened')
    yield from null() 
try:
    suspender_shb = SuspendBoolHigh(user_ns['shb'].state, sleep=5)
                                    #pre_plan=tell_slack_shb_closed,
                                    #post_plan=tell_slack_shb_opened,)
    all_BMM_suspenders.append(suspender_shb)
except Exception as e:
    error_msg(f'failed to create shb suspender: {e}')
    pass

    
def BMM_suspenders():
    BMMuser = user_ns['BMMuser']
    if BMMuser.suspenders_engaged:
        return
    for s in all_BMM_suspenders:
        user_ns['RE'].install_suspender(s)
    BMMuser.suspenders_engaged = True

def BMM_clear_suspenders():
    RE = user_ns['RE']
    BMMuser = user_ns['BMMuser']
    if BMMuser.running_macro is False:
        RE.clear_suspenders()
        BMMuser.suspenders_engaged = False


def BMM_clear_to_start():
    ok = True
    text = ''
    # return (ok, text)
    if user_ns['ring'].current.get() < 10:
        ok = False
        text += 'There is no current in the storage ring. Solution: wait for beam to come back\n'
    if user_ns['bmps'].state.get() == 0:
        ok = False
        text += 'BMPS is closed. Solution: check vacuum levels and gate valves, then call the control room and ask to have it opened\n'
    if user_ns['idps'].state.get() == 0:
        ok = False
        text += 'Front end shutter (sha) is closed. Solution: search the FOE then do sha.open()\n'
    if user_ns['shb'].state.get() == 1:
        #action = input(bold_msg("\nB shutter is closed.  Open shutter? " + PROMPT)).strip()
        print()
        action = animated_prompt('B shutter is closed.  Open shutter? ' + PROMPTNC).strip()
        openit = False
        if action == '' or action[0].lower() == 'y':
            openit = True
        else:
            openit = False
        if openit == True:
            user_ns['shb'].open()
        if user_ns['shb'].state.get() == 1:  # B shutter failed to open
            ok = False
            text += 'Photon shutter (shb) is closed. Solution: search the hutch then do shb.open()\n'
        else:                   # B shutter successfully opened
            ok = True
    # if quadem1.I0.get() < 0.1:
    #     ok = 0
    #     text += 'There is no signal on I0\n'
    return (ok, text)
