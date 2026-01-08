
try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False

import bluesky

from bluesky.plans import rel_scan
from bluesky.plan_stubs import sleep, mv, null, mvr
from bluesky import __version__ as bluesky_version
import numpy, os, datetime
from lmfit.models import SkewedGaussianModel, RectangleModel
#from databroker.core import SingleRunCache
import matplotlib
import matplotlib.pyplot as plt



from bluesky.preprocessors import subs_decorator, finalize_wrapper

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from bmm_tools.tools.misc     import now
from bmm_tools.tools.messages import *  # error_msg et al. + boxedtext
from bmm_tools.tools.animated_prompt import PROMPTNC, animated_prompt

from BMM.resting_state import resting_state_plan
from BMM.logging       import BMM_log_info, BMM_msg_hook
from BMM.functions     import clean_img, PROMPT
from BMM.workspace     import rkvs

from BMM.user_ns.base        import WORKSPACE
from BMM.user_ns.bmm         import BMMuser, kafka
from BMM.user_ns.dcm         import dcm
from BMM.user_ns.detectors   import quadem1, ic0, ic1, ic2, xs, xs1, xs4, xs7, pilatus, eiger, dante, ION_CHAMBERS
from BMM.user_ns.dwelltime   import _locked_dwell_time, with_xspress3, with_quadem, with_struck, use_7element, use_4element, use_1element
from BMM.user_ns.dwelltime   import with_ic0, with_ic1, with_ic2
from BMM.user_ns.instruments import m2, m3, slits3, xafs_wheel
from BMM.user_ns.motors      import *
from BMM.user_ns.suspenders  import suspenders

def get_mode():
    if m2.vertical.readback.get() < 0: # this is a focused mode
        if m2.pitch.readback.get() > 3:
            return 'XRD'
        else:
            if m3.vertical.readback.get() > -2:
                return 'A'
            elif m3.vertical.readback.get() > -7:
                return 'B'
            else:
                return 'C'
    else:
        if m3.pitch.readback.get() < 3:
            return 'F'
        elif m3.lateral.readback.get() > 0:
            return 'D'
        else:
            return 'E'

def unset_mouse_click():
    rkvs.set('BMM:mouse_event:value',  '')
    rkvs.set('BMM:mouse_event:motor',  '')
    rkvs.set('BMM:mouse_event:value2', '')
    rkvs.set('BMM:mouse_event:motor2', '')



    
def pluck(suggested_motor=None):
    '''Negotiate an interaction with the Kafka consumer to pluck a
    position from a plot, stash that position and its motor in Redis,
    then grab that position from Redis.  Assuming the motor name can
    be correlated to an actual motor, offer to move the motor to that
    position.

    This is a bit cumbersome.  Some notes on that:

    (1) Using Kafka to communicate from the consumer back to the bsui
    process would be sensible.  But that would require putting the
    consumer on a thread, which Bruce doesn't really understand.

    (2) The additional check to verify the movement before moving
    might get annoying, however it is easy to attach a callback to
    every plot that the Kafka consumer makes.  Thus, any plot that
    still exists on screen can be plucked from.  The additional
    handshake with the user is required to make sure that the plucked
    value makes sense -- it is possible to pluck from a very stale
    plot!

    (3) There is a hard-coded 20 second time out on clicking on a
    plot.  There has to be a timeout to avoid blocking forever.

    '''

    unset_mouse_click()
    user_ns['BMMuser'].mouse_click = None
    print('\nSingle click the left mouse button on the plot to pluck a point (you have 20 seconds)...')
    count = 0
    while rkvs.get('BMM:mouse_event:value').decode('utf-8') == '':
        yield from sleep(0.25)
        count = count + 1
        if count > 80:
            print('Timing out...')
            return(yield from null())
    
    yield from sleep(0.25)
    position    = float(rkvs.get('BMM:mouse_event:value').decode('utf-8'))
    motor_name  = rkvs.get('BMM:mouse_event:motor').decode('utf-8')
    if motor_name == 'xafs_yu':
        motor_name = 'xafs_table_yu'
    position2   = ''
    motor_name2 = ''
    if rkvs.get('BMM:mouse_event:value2').decode('utf-8') != '':
        position2   = float(rkvs.get('BMM:mouse_event:value2').decode('utf-8'))
        motor_name2 = rkvs.get('BMM:mouse_event:motor2').decode('utf-8')
    motor  = None
    motor2 = None

    # need to parse areascan axis labels
    if 'fast axis' in motor_name:
        motor_name = motor_name.split()[2][1:-1]
    if 'slow axis' in motor_name2:
        motor_name2 = motor_name2.split()[2][1:-1]

    
    if suggested_motor is not None and suggested_motor.name != motor_name:
        warning_msg(f'You seem to have clicked on the wrong plot.')
        warning_msg(f'You just scanned {suggested_motor.name} but clicked on a window showing {motor.name}.')
        unset_mouse_click()
        return(yield from null())
        
    for m in user_ns['sd'].baseline:
        if m.name == motor_name:
            motor = m
            break
    if motor_name2 != '':
        for m in user_ns['sd'].baseline:
            if m.name == motor_name2:
                motor2 = m
                break

    if motor == None:
        warning_msg(f'{motor_name} does not seem to be the name of an actual motor ...  hmmm....')
        unset_mouse_click()
        return(yield from null())
    ## if x-axis motor is ok, presume that y-axis will also be sensible

    question = f'\nMove {motor_name} to {position:.3f} ? '
    if motor2 is not None:
        question = f'\nMove ({motor_name}, {motor_name2}) to ({position:.3f}, {position2:.3f}) ? '
        
    
    #action = input(question + PROMPT)
    print()
    action = animated_prompt(question + PROMPTNC)
    if action != '':
        if action[0].lower() == 'n' or action[0].lower() == 'q':
            print('Skipping...')
            unset_mouse_click()
            return(yield from null())
    #print(motor.name, motor_name, position)
    if motor2 is None:
        yield from mv(motor, position)
    else:
        yield from mv(motor, position, motor2, position2)
    user_ns['BMMuser'].mouse_click = position
    unset_mouse_click()
    whisper('\nRE(pluck()) to grab a different point from the plot.\n')
    

        

from scipy.ndimage import center_of_mass
def com(signal):
    '''Return the center of mass of a 1D array. This is used to find the
    center of rocking curve and slit height scans.'''
    return int(center_of_mass(signal)[0])
import pandas
def peak(signal):
    '''Return the index of the maximum of a 1D array. This is used to find the
    center of rocking curve and slit height scans.'''
    return pandas.Series.idxmax(signal)


def wiggle_bct(tries=3):
    '''Wiggle the dm3_bct motor to see if it prompts an amplifier fault.
    If it does, cycle the dm3 killswitch.  Give up after a few tries.

    '''
    attempt = 1
    while attempt <= tries:
        try:
            yield from mvr(dm3_bct, 0.01)
        except:
            pass
        if dm3_bct.amfe.get() or dm3_bct.amfae.get():
            warning_msg(f'Amplifier fault on dm3_bct.  Attempt {attempt} to clear fault.')
            user_ns['ks'].cycle('dm3')
            attempt += 1
        else:
            return True
    else:
        if dm3_bct.amfe.get() or dm3_bct.amfae.get():
            error_msg('Unable to start slit height scan.  Amplifier fault on dm3_bct.')
            yield from null()
            return False

UNSET_PEAK_POSITION = -10_000_000_000
        
def prepare_alignment_scan(inttime=0.1):
    '''Prepare for an alignment scan:

    1. Set the redis parameter used to communicate the alignment
       result back from the Kafka client to its unset value.

    2. Set the dwell time to a value suitable for an alignment scan.
       The default is 0.1 seconds, but can be overwritten if need be.

    '''
    rkvs.set('BMM:peakposition', UNSET_PEAK_POSITION - 0.1)
    yield from mv(_locked_dwell_time, inttime)
    

def fetch_peak_position_via_redis(maxtries=6, verbose=False):
    '''Retrieve a result found by the Kafka consumer and posted to redis.

    The function prepare_alignment_scan() should have been called
    prior to the alignment scan.

    This function waits increasing times for that parameter in redis
    to be set to value bigger than UNSET_PEAK_POSITION.  This gives
    the kafka consumer some time to do its thing.

    '''
    time.sleep(0.25)
    top = float(rkvs.get('BMM:peakposition').decode('utf8'))
    count = 0
    #if verbose: print(f"{count = }, {top = }")
    while top < UNSET_PEAK_POSITION:
        time.sleep(0.1 * 2**count)
        top = float(rkvs.get('BMM:peakposition').decode('utf8'))
        count += 1
        if verbose: print(f"{count = }, {top = }", flush=True)
        if count > maxtries:
            return(None)
    return top

def slit_height(start=-1.5, stop=1.5, nsteps=31, move=False, force=False, slp=1.0, choice='peak'):
    '''Perform a relative scan of the DM3 BCT motor around the current
    position to find the optimal position for slits3. Optionally, the
    motor will moved to the center of mass of the peak at the end of
    the scan.

    Parameters
    ----------
    start : float
        starting position relative to current [-3.0]
    end : float 
        ending position relative to current [3.0]
    nsteps : int
        number of steps [61]
    move : bool
        True=move to position of max signal, False=pluck and move [False]
    slp : float
        length of sleep before trying to move dm3_bct [3.0]
    choice : str 
        'peak' or 'com' (center of mass) ['peak']
    '''
    def main_plan(start, stop, nsteps, move, slp, force):
        (ok, text) = suspenders.clear_to_start()
        if force is False and ok is False:
            error_msg(text)
            yield from null()
            return

        user_ns['RE'].msg_hook = None
        BMMuser.motor = dm3_bct
        line1 = '%s, %s, %.3f, %.3f, %d -- starting at %.3f\n' % \
                (motor.name, 'i0', start, stop, nsteps, motor.user_readback.get())
        rkvs.set('BMM:scan:type',      'line')
        rkvs.set('BMM:scan:starttime', str(datetime.datetime.timestamp(datetime.datetime.now())))
        rkvs.set('BMM:scan:estimated', 0)

        def scan_slit(slp):

            #if slit_height < 0.5:
            #    yield from mv(slits3.vsize, 0.5)

            yield from prepare_alignment_scan()
            #rkvs.set('BMM:peakposition', -10_000_000_000.1)
            #yield from mv(_locked_dwell_time, 0.1)
            yield from mv(motor.velocity, 0.4)
            yield from mv(motor.kill_cmd, 1)


            ok = yield from wiggle_bct()
            if ok is False:
                return(yield from null())

            kafka.message({'linescan': 'start',
                           'motor' : motor.name,
                           'detector' : 'I0',
                           'fluo_detector': None,})
            uid = yield from rel_scan([*ION_CHAMBERS], motor, start, stop, nsteps, md={'plan_name' : f'rel_scan linescan {motor.name} I0'})
            kafka.message({'linescan': 'stop',})
            
            user_ns['RE'].msg_hook = BMM_msg_hook
            BMM_log_info(f'slit height scan: {line1}\tuid = {uid}')
            if motor.amfe.get() or motor.amfae.get():
                user_ns['ks'].cycle('dm3')
            if move:
                kafka.message({'close': 'last'})
                kafka.message({'peakfit' : True,
                               'uid' : uid,
                               'motor_name' : motor.name,
                               'signal' : 'I0',
                               'choice' : choice})
                top = fetch_peak_position_via_redis()
                if top is None:
                    error_msg('Failed to find rocking curve peak position.')
                    raise ValueError('Failed to find slit_height peak position.')
                yield from mv(motor, top)
                
            else:
                #action = input('\n' + bold_msg('Pluck motor position from the plot? ' + PROMPT))
                print()
                action = animated_prompt('Pluck motor position from the plot? ' + PROMPTNC)
                if action != '':
                    if action[0].lower() == 'n' or action[0].lower() == 'q':
                        return(yield from null())
                yield from sleep(slp)
                yield from mv(motor.kill_cmd, 1)
                #yield from mv(motor.inpos, 1)
                yield from sleep(slp)
                yield from pluck(suggested_motor=motor)
                #yield from move_after_scan(motor)
        yield from scan_slit(slp)

    def cleanup_plan(slp):
        #yield from mv(slits3.vsize, slit_height)
        yield from mv(_locked_dwell_time, 0.5)
        yield from sleep(slp)
        yield from mv(motor.kill_cmd, 1)
        yield from resting_state_plan()


    #######################################################################
    # this is a tool for verifying a macro.  this replaces this slit      #
    # height scan with a sleep, allowing the user to easily map out motor #
    # motions in a macro                                                  #
    if BMMuser.macro_dryrun:
        info_msg('\nBMMuser.macro_dryrun is True.  Sleeping for %.1f seconds rather than running a slit height scan.\n' %
                 BMMuser.macro_sleep)
        countdown(BMMuser.macro_sleep)
        return(yield from null())
    #######################################################################
    motor = dm3_bct
    slit_height = slits3.vsize.readback.get()
    user_ns['RE'].msg_hook = None
    yield from finalize_wrapper(main_plan(start, stop, nsteps, move, slp, force), cleanup_plan(slp))
    user_ns['RE'].msg_hook = BMM_msg_hook
        

def mirror_pitch(start=None, stop=None, nsteps=41, mirror='m3', move=False, force=False, choice='peak'):
    '''Perform a relative scan of the m3.yu (or m2.yu) motor around the
    current position to find the optimal position for mirror
    pitch. This is run after positioning the DM3 BCT (slit_height)
    motor for the current photon delivery mode.  This scan tweaks the
    mirror pitch to center the beam on the slit height position.

    Optionally, the motor will moved to the peak at the end of the
    scan.

    Parameters
    ----------
    start : float
        starting position relative to current [-3.0]
    end : float 
        ending position relative to current [3.0]
    nsteps : int
        number of steps [61]
    move : bool
        True=move to position of max signal, False=pluck and move [False]
    force : bool
        True=run scan even if not clear to start, False=respect clear-to-start [False]
    choice : str 
        'peak' or 'com' (center of mass) ['peak']  (com not currently implemented)

    '''

    def main_plan(start, stop, nsteps, move, force):
        (ok, text) = suspenders.clear_to_start()
        if force is False and ok is False:
            error_msg(text)
            yield from null()
            return

        user_ns['RE'].msg_hook = None
        BMMuser.motor = dm3_bct
        line1 = '%s, %s, %.3f, %.3f, %d -- starting at %.3f\n' % \
                (motor.name, 'i0', start, stop, nsteps, motor.user_readback.get())
        rkvs.set('BMM:scan:type',      'line')
        rkvs.set('BMM:scan:starttime', str(datetime.datetime.timestamp(datetime.datetime.now())))
        rkvs.set('BMM:scan:estimated', 0)

        def scan_pitch():

            #if slit_height < 0.5:
            #    yield from mv(slits3.vsize, 0.5)

            yield from prepare_alignment_scan()
            #rkvs.set('BMM:peakposition', -10_000_000_000.1)
            #yield from mv(_locked_dwell_time, 0.1)

            kafka.message({'linescan': 'start',
                           'motor' : motor.name,
                           'detector' : 'I0',
                           'fluo_detector': None,})
            uid = yield from rel_scan([*ION_CHAMBERS], motor, start, stop, nsteps, md={'plan_name' : f'rel_scan linescan {motor.name} I0'})
            kafka.message({'linescan': 'stop',})
            
            user_ns['RE'].msg_hook = BMM_msg_hook
            BMM_log_info(f'mirror pitch scan: {line1}\tuid = {uid}')
            if move:
                kafka.message({'close': 'last'})
                kafka.message({'peakfit' : True,
                               'uid' : uid,
                               'motor_name' : motor.name,
                               'signal' : 'I0',
                               'choice' : choice})
                top = fetch_peak_position_via_redis()
                if top is None:
                    error_msg('Failed to find rocking curve peak position.')
                    raise ValueError('Failed to find rocking curve peak position.')
                yield from mv(motor, top)

            else:
                #action = input('\n' + bold_msg('Pluck motor position from the plot? ' + PROMPT))
                print()
                action = animated_prompt('Pluck motor position from the plot? ' + PROMPTNC)
                if action != '':
                    if action[0].lower() == 'n' or action[0].lower() == 'q':
                        return(yield from null())
                yield from pluck(suggested_motor=motor)
        yield from scan_pitch()

    def cleanup_plan():
        #yield from mv(slits3.vsize, slit_height)
        yield from mv(_locked_dwell_time, 0.5)
        yield from resting_state_plan()

    #######################################################################
    # this is a tool for verifying a macro.  this replaces this slit      #
    # height scan with a sleep, allowing the user to easily map out motor #
    # motions in a macro                                                  #
    if BMMuser.macro_dryrun:
        info_msg('\nBMMuser.macro_dryrun is True.  Sleeping for %.1f seconds rather than running a slit height scan.\n' %
                 BMMuser.macro_sleep)
        countdown(BMMuser.macro_sleep)
        return(yield from null())
    #######################################################################
    if mirror == 'm2':
        motor, defstart, defstop = m2.yu, -0.06, 0.06
    else:
        motor, defstart, defstop = m3.yu, -0.1, 0.1
    if start is None:
        start = defstart
    if stop is None:
        stop  = defstop
    user_ns['RE'].msg_hook = None
    yield from finalize_wrapper(main_plan(start, stop, nsteps, move, force), cleanup_plan())
    user_ns['RE'].msg_hook = BMM_msg_hook


def rocking_curve(start=-0.10, stop=0.10, nsteps=101, detector='I0', choice='peak', height=3):
    '''Perform a relative scan of the DCM 2nd crystal pitch around the current
    position to find the peak of the crystal rocking curve.  Begin by opening
    the hutch slits to 3 mm. At the end, move to the position of maximum 
    intensity on I0, then return to the hutch slits to their original height.

    Parameters
    ----------
    start : (float)
        starting position relative to current [-0.1]
    end : (float)
        ending position relative to current [0.1]
    nsteps : (int)
        number of steps [101]
    detector : (string)
        'I0' or 'Bicron' ['I0']
    choice : (string)
        'peak', fit' or 'com' (center of mass) ['peak']
    height : float
        slit3 height during rocking curve scan [3]

    If choice is fit, the fit is performed using the
    SkewedGaussianModel from lmfit, which works pretty well for this
    measurement at BMM.  The line shape is a bit skewed due to the
    convolution with the slightly misaligned entrance slits.

    '''
    def main_plan(start, stop, nsteps, detector, height):
        (ok, text) = suspenders.clear_to_start()
        if ok is False:
            error_msg(text)
            yield from null()
            return

        user_ns['RE'].msg_hook = None
        BMMuser.motor = motor
    
        if detector.lower() == 'bicron':
            sgnl = 'Bicron'
            titl = 'Bicron signal vs. DCM 2nd crystal pitch'
        else:
            sgnl = 'I0'
            titl = 'I0 signal vs. DCM 2nd crystal pitch'


        rkvs.set('BMM:scan:type',      'line')
        rkvs.set('BMM:scan:starttime', str(datetime.datetime.timestamp(datetime.datetime.now())))
        rkvs.set('BMM:scan:estimated', 0)

        def scan_dcmpitch(sgnl):
            line1 = '%s, %s, %.3f, %.3f, %d -- starting at %.3f\n' % \
                    (motor.name, sgnl, start, stop, nsteps, motor.user_readback.get())

            yield from prepare_alignment_scan()
            #rkvs.set('BMM:peakposition', -10_000_000_000.1)
            #yield from mv(_locked_dwell_time, 0.1)
            yield from dcm.kill_plan()

            yield from mv(slits3.top, height/2.0, slits3.bottom, -1*height/2.0)
            #if sgnl == 'Bicron':
            #    yield from mv(slitsg.vsize, 5)
                
            dets = ION_CHAMBERS.copy()
            kafka.message({'linescan': 'start',
                           'motor' : motor.name,
                           'detector' : 'I0',
                           'fluo_detector': None,})
            uid = yield from rel_scan(dets, motor, start, stop, nsteps, md={'plan_name' : f'rel_scan linescan {motor.name} I0'})
            kafka.message({'linescan': 'stop',})
            kafka.message({'close': 'last'})
            kafka.message({'peakfit' : True,
                           'uid' : uid,
                           'motor_name' : 'dcm_pitch',
                           'signal' : 'I0',
                           'choice' : choice})

            top = fetch_peak_position_via_redis()
            if top is None:
                error_msg('Failed to find rocking curve peak position.')
                raise ValueError('Failed to find rocking curve peak position.')
                
            yield from mv(motor.kill_cmd, 1)
            yield from sleep(1.0)
            user_ns['RE'].msg_hook = BMM_msg_hook

            #BMM_log_info('rocking curve scan: %s\tuid = %s, scan_id = %d' %
            #             (line1, uid, user_ns['db'][-1].start['scan_id']))
            BMM_log_info(f'rocking curve scan: {line1}\tuid = {uid}')
            yield from mv(motor, top)
            #if sgnl == 'Bicron':
            #    yield from mv(slitsg.vsize, gonio_slit_height)
        yield from scan_dcmpitch(sgnl)

    def cleanup_plan():
        yield from mv(slits3.top, slit_height/2, slits3.bottom, -1*slit_height/2)
        #yield from mv(slits3.vsize, slit_height)
        yield from mv(_locked_dwell_time, 0.5)
        yield from sleep(1.0)
        yield from mv(motor.kill_cmd, 1)
        yield from sleep(1.0)
        yield from dcm.kill_plan()
        yield from resting_state_plan()

    
    ######################################################################
    # this is a tool for verifying a macro.  this replaces this rocking  #
    # curve scan with a sleep, allowing the user to easily map out motor #
    # motions in a macro                                                 #
    if BMMuser.macro_dryrun:
        info_msg('\nBMMuser.macro_dryrun is True.  Sleeping for %.1f seconds rather than running a rocking curve scan.\n' %
                 BMMuser.macro_sleep)
        countdown(BMMuser.macro_sleep)
        return(yield from null())
    ######################################################################
    motor = dcm.pitch
    slit_height = slits3.vsize.readback.get()
    # try:
    #     gonio_slit_height = slitsg.vsize.readback.get()
    # except:
    #     gonio_slit_height = 1
    user_ns['RE'].msg_hook = None
    yield from finalize_wrapper(main_plan(start, stop, nsteps, detector, height), cleanup_plan())
    user_ns['RE'].msg_hook = BMM_msg_hook




def hcenter(start=-1, stop=1, nsteps=41, move=False, force=False, choice='peak'):
    '''Perform a relative scan of slits3.hcenter to optimize the signal on
    I0.

    Optionally, the motor will moved to the peak at the end of the
    scan.

    Parameters
    ----------
    start : float
        starting position relative to current [-3.0]
    end : float 
        ending position relative to current [3.0]
    nsteps : int
        number of steps [61]
    move : bool
        True=move to position of max signal, False=pluck and move [False]
    force : bool
        True=run scan even if not clear to start, False=respect clear-to-start [False]
    choice : str 
        'peak' or 'com' (center of mass) ['peak']  (com not currently implemented)

    '''

    def main_plan(start, stop, nsteps, move, force):
        (ok, text) = suspenders.clear_to_start()
        if force is False and ok is False:
            error_msg(text)
            yield from null()
            return

        user_ns['RE'].msg_hook = None
        line1 = '%s, %s, %.3f, %.3f, %d -- starting at %.3f\n' % \
                (motor.name, 'i0', start, stop, nsteps, motor.position)
        rkvs.set('BMM:scan:type',      'line')
        rkvs.set('BMM:scan:starttime', str(datetime.datetime.timestamp(datetime.datetime.now())))
        rkvs.set('BMM:scan:estimated', 0)

        def scan_hcenter():
            yield from prepare_alignment_scan()
            #rkvs.set('BMM:peakposition', -10_000_000_000.1)
            #yield from mv(_locked_dwell_time, 0.1)

            kafka.message({'linescan': 'start',
                           'motor' : motor.name,
                           'detector' : 'I0',
                           'fluo_detector': None,})
            uid = yield from rel_scan([*ION_CHAMBERS], motor, start, stop, nsteps, md={'plan_name' : f'rel_scan linescan {motor.name} I0'})
            kafka.message({'linescan': 'stop',})
            
            user_ns['RE'].msg_hook = BMM_msg_hook
            BMM_log_info(f'hcenter scan: {line1}\tuid = {uid}')
            if move:
                kafka.message({'close': 'last'})
                kafka.message({'peakfit' : True,
                               'uid' : uid,
                               'motor_name' : motor.name,
                               'signal' : 'I0',
                               'choice' : choice})
                top = fetch_peak_position_via_redis()
                if top is None:
                    error_msg('Failed to find rocking curve peak position.')
                    raise ValueError('Failed to find rocking curve peak position.')
                yield from mv(motor, top)

            else:
                #action = input('\n' + bold_msg('Pluck motor position from the plot? ' + PROMPT))
                print()
                action = animated_prompt('Pluck slits3.hcenter position from the plot? ' + PROMPTNC)
                if action != '':
                    if action[0].lower() == 'n' or action[0].lower() == 'q':
                        return(yield from null())
                yield from pluck(suggested_motor=motor)
        yield from scan_hcenter()

    def cleanup_plan():
        yield from mv(_locked_dwell_time, 0.5)
        yield from resting_state_plan()

    #######################################################################
    # this is a tool for verifying a macro.  this replaces this slit      #
    # height scan with a sleep, allowing the user to easily map out motor #
    # motions in a macro                                                  #
    if BMMuser.macro_dryrun:
        info_msg('\nBMMuser.macro_dryrun is True.  Sleeping for %.1f seconds rather than running an hcenter scan.\n' %
                 BMMuser.macro_sleep)
        countdown(BMMuser.macro_sleep)
        return(yield from null())
    #######################################################################
    motor = slits3.hcenter
    user_ns['RE'].msg_hook = None
    yield from finalize_wrapper(main_plan(start, stop, nsteps, move, force), cleanup_plan())
    user_ns['RE'].msg_hook = BMM_msg_hook





    
def find_slot(shape='slot'):
    ## NEVER prompt when using queue server
    if is_re_worker_active() is True:
        BMMuser.prompt = False
    if BMMuser.prompt:
        #action = input("\nIs the beam currently on a slot in the outer ring? " + PROMPT)
        print()
        action = animated_prompt('Is the beam currently on a slot in the outer ring? ' + PROMPTNC)
        if action != '':
            if action[0].lower() == 'n' or action[0].lower() == 'q':
                return(yield from null())
    
    kafka.message({'align_wheel' : 'start'})
    if shape == 'circle':
        yield from rectangle_scan(motor=xafs_y, start=-10,  stop=10,  nsteps=31, detector='It', chore='find_slot')
    else:
        yield from rectangle_scan(motor=xafs_y, start=-3,  stop=3,  nsteps=31, detector='It', chore='find_slot')
    #kafka.message({'close': 'all'})
    yield from rectangle_scan(motor=xafs_x, start=-10, stop=10, nsteps=31, detector='It', chore='find_slot')
                              #md={'BMM_kafka': {'hint': f'rectanglescan It xafs_x notnegated'}})
    user_ns['xafs_wheel'].in_place()
    kafka.message({'close': 'all'})
    kafka.message({'align_wheel' : 'stop'})
    bold_msg(f'Found slot at (X,Y) = ({xafs_x.position}, {xafs_y.position})')

def find_reference():
    yield from rectangle_scan(motor=xafs_refy, start=-4,   stop=4,   nsteps=31, detector='Ir')
                              #md={'BMM_kafka': {'hint': f'rectanglescan Ir xafs_refy notnegated'}})
    yield from rectangle_scan(motor=xafs_refx, start=-10,  stop=10,  nsteps=31, detector='Ir')
                              #md={'BMM_kafka': {'hint': f'rectanglescan Ir xafs_refx notnegated'}})
    bold_msg(f'Found reference slot at (X,Y) = ({xafs_refx.position}, {xafs_refy.position})')

    
def rectangle_scan(motor=None, start=-20, stop=20, nsteps=41, detector='It',
                   negate=False, filename=None, move=True, force=False, chore='', md={}):

    def main_plan(motor, start, stop, nsteps, detector, negate, filename, move, force, chore, md):
        if force is False:
            (ok, text) = suspenders.clear_to_start()
            if ok is False:
                error_msg(text)
                yield from null()
                return

        user_ns['RE'].msg_hook = None
        BMMuser.motor = motor

        dets = ION_CHAMBERS.copy()

        sgnl = 'fluorescence (Xspress3)'

        if detector.lower() == 'if':
            dets.append(user_ns['xs'])
            sgnl = 'fluorescence (Xspress3)'
            yield from mv(xs.total_points, nsteps)
        elif detector.lower() == 'it':
            sgnl = 'transmission'
        elif detector.lower() == 'ir':
            sgnl = 'reference'

        titl = f'{sgnl} vs. {motor.name}'

        rkvs.set('BMM:scan:type',      'line')
        rkvs.set('BMM:scan:starttime', str(datetime.datetime.timestamp(datetime.datetime.now())))
        rkvs.set('BMM:scan:estimated', 0)
        
        def doscan(filename):
            line1 = '%s, %s, %.3f, %.3f, %d -- starting at %.3f\n' % \
                    (motor.name, sgnl, start, stop, nsteps, motor.user_readback.get())
            
            if negate == True:
                hint = f'rectanglescan {detector.capitalize()} {motor.name} negated'
            else:
                hint = f'rectanglescan {detector.capitalize()} {motor.name} notnegated'
            if 'BMM_kafka' not in md:
                md['BMM_kafka'] = dict()
            if 'hint' not in md['BMM_kafka']:
                md['BMM_kafka']['hint'] = hint

            fluo_detector = None
            if detector.lower() == 'if':
                fluo_detector = user_ns['xs'].name
            elif detector == 'Dante':
                fluo_detector = 'Dante'
            yield from prepare_alignment_scan()
            kafka.message({'linescan'      : 'start',
                           'motor'         : motor.name,
                           'detector'      : detector.capitalize(),
                           'fluo_detector' : fluo_detector,})
            uid = yield from rel_scan(dets, motor, start, stop, nsteps, md={**md, 'plan_name' : f'rel_scan linescan {motor.name} I0'})
            kafka.message({'linescan': 'stop',})

            if move is True:
                kafka.message({'close': 'all'})
                kafka.message({'rectanglefit' : True,
                               'uid'          : uid,
                               'signal'       : detector.capitalize(),
                               'motor_name'   : motor.name })

                top = fetch_peak_position_via_redis(verbose=True)
                if top is None:
                    error_msg('Failed to find rectangle midpoint.')
                    raise ValueError('Failed to find rectangle midpoint.')
                yield from mv(motor, top)
                bold_msg(f'Found center at {motor.name} = {motor.position}')
            else:
                print()
                action = animated_prompt('Pluck motor position from the plot? ' + PROMPTNC)
                if action != '':
                    if action[0].lower() == 'n' or action[0].lower() == 'q':
                        return(yield from null())
                yield from pluck(suggested_motor=motor)

        yield from doscan(filename)
        
    def cleanup_plan():
        yield from resting_state_plan()
    
    user_ns['RE'].msg_hook = None
    yield from finalize_wrapper(main_plan(motor, start, stop, nsteps, detector, negate, filename, move, force, chore, md),
                                cleanup_plan())
    user_ns['RE'].msg_hook = BMM_msg_hook


def peak_scan(motor=None, start=-20, stop=20, nsteps=41, detector='It', find='max', how='peak', filename=None):
    ''' Deprecated. needs to be updated for the kafka/data seucrity agent_change_edge
    '''
    def main_plan(motor, start, stop, nsteps, detector, find, how, filename):
        (ok, text) = suspenders.clear_to_start()
        if ok is False:
            error_msg(text)
            yield from null()
            return

        user_ns['RE'].msg_hook = None
        BMMuser.motor = motor

        dets = ION_CHAMBERS.copy()

        sgnl = 'fluorescence (Xspress3)'

        if detector.lower() == 'if':
            dets.append(user_ns['xs'])
            sgnl = 'fluorescence (Xspress3)'
            yield from mv(xs.total_points, nsteps)
        elif detector.lower() == 'it':
            dets.append(user_ns['ic1'])
            sgnl = 'transmission'
        elif detector.lower() == 'ir':
            dets.append(user_ns['ic1'])
            dets.append(user_ns['ic2'])
            sgnl = 'reference'

        titl = f'{sgnl} vs. {motor.name}'

        rkvs.set('BMM:scan:type',      'line')
        rkvs.set('BMM:scan:starttime', str(datetime.datetime.timestamp(datetime.datetime.now())))
        rkvs.set('BMM:scan:estimated', 0)

        def doscan(filename):
            line1 = '%s, %s, %.3f, %.3f, %d -- starting at %.3f\n' % \
                    (motor.name, sgnl, start, stop, nsteps, motor.user_readback.get())

            yield from prepare_alignment_scan()
            if plotting_mode(p['mode']) == 'fluorescence':
                fluo_detector = user_ns['xs'].name
            elif detector == 'Dante':
                fluo_detector = 'Dante'
            kafka.message({'linescan': 'start',
                           'motor' : motor.name,
                           'detector' : detector.capitalize(),
                           'fluo_detector': fluo_detector,})
            uid = yield from rel_scan(dets, motor, start, stop, nsteps, md={'plan_name' : f'rel_scan linescan {motor.name} I0'})
            kafka.message({'linescan': 'stop',})

            kafka.message({'stepfit'    : True,
                           'uid'        : uid,
                           'motor_name' : motor.name,
                           'signal'     : 'It',
                           'choice'     : 'peak'})
            target = fetch_peak_position_via_redis()
            yield from mv(motor, target)
            bold_msg(f'Found peak at {motor.name} = {motor.position}')
            for k in ('center1', 'center2', 'sigma1', 'sigma2', 'amplitude', 'midpoint'):
                rkvs.set(f'BMM:lmfit:{k}', out.params[k].value)

        yield from doscan(filename)
        
    def cleanup_plan():
        yield from resting_state_plan()
    
    user_ns['RE'].msg_hook = None
    yield from finalize_wrapper(main_plan(motor, start, stop, nsteps, detector, find, filename), cleanup_plan())
    user_ns['RE'].msg_hook = BMM_msg_hook


    

##                     linear stages        tilt stage           rotation stages
motor_nicknames = {'x'    : xafs_x,     'roll' : xafs_roll,
                   'y'    : xafs_y,     'pitch': xafs_pitch, 'wh' : xafs_wheel,
                   #'s'    : xafs_lins,
                   'p'    : xafs_pitch, 'rs' : xafs_rots,
                   'xs'   : xafs_linxs, 'r'    : xafs_roll,
               }

## before 29 August 2018, the order of arguments for linescan() was
##   linescan(axis, detector, ...)
## now it is
##   linescan(detector, axis, ...)
## for consistency with areascan().  This does a simple check to see if the old
## argument order is being used and swaps them if need be
def ls_backwards_compatibility(detin, axin):
    if type(axin) is str and axin.capitalize() in ('It', 'If', 'I0', 'Iy', 'Pips', 'Ir', 'Both',
                                                   'I0a', 'I0b', 'Ic0', 'Ic1',
                                                   'Xs', 'Xs1', 'Xs4', 'Xs7', 'Pilatus', 'Eiger', 'Dante'):
        return(axin, detin)
    else:
        return(detin, axin)


#mytable = None
####################################
# generic linescan vs. It/If/Ir/I0 #
####################################
def linescan(detector, axis, start, stop, nsteps, dopluck=True, force=False, stack=True, inttime=0.1, md={}): # integration time?
    '''
    Generic linescan plan.  This is a RELATIVE scan, relative to the
    current position of the selected motor.

    Examples
    --------

    >>> RE(linescan('it', 'x', -1, 1, 21))

    Parameters
    ----------
    detector : str
        detector to display -- if, it, ir, or i0
    axis : str or EpicsMotor
        motor or nickname
    start : float
        starting value for a relative scan
    stop : float
         ending value for a relative scan
    nsteps : int
        number of steps in scan
    dopluck : bool, optional
        flag for whether to offer to pluck & move motor
    force : bool, optional
        flag for forcing a scan even if not clear to start
    stack : bool, optional
        flag for forcing a fluorescence, yield, etc scan to plot without stacking with I0
    inttime : float, optional
        integration time in seconds (default: 0.1)

    The motor is either the BlueSky name for a motor (e.g. xafs_linx)
    or a nickname for an XAFS sample motor (e.g. 'x' for xafs_linx).

    This does not write an ASCII data file, but it does make a log entry.

    Use the ls2dat() function to extract the linescan from the
    database and write it to a file.
    '''

    def main_plan(detector, axis, start, stop, nsteps, dopluck, force, stack, md):
        if force is False:
            (ok, text) = suspenders.clear_to_start()
            if ok is False:
                error_msg(text)
                yield from null()
                return

        detector, axis = ls_backwards_compatibility(detector, axis)
        # print('detector is: ' + str(detector))
        # print('axis is: ' + str(axis))
        # return(yield from null())

        user_ns['RE'].msg_hook = None
        ## sanitize input and set thismotor to an actual motor
        if type(axis) is str: axis = axis.lower()
        detector = detector.capitalize()

        ## sanity checks on axis
        if axis not in motor_nicknames.keys() and 'EpicsMotor' not in str(type(axis)) \
           and 'PseudoSingle' not in str(type(axis)) and 'WheelMotor' not in str(type(axis)):
            error_msg('\n*** %s is not a linescan motor (%s)\n' %
                      (axis, str.join(', ', motor_nicknames.keys())))
            yield from null()
            return

        if 'EpicsMotor' in str(type(axis)):
            thismotor = axis
        elif 'PseudoSingle' in str(type(axis)):
            thismotor = axis
        elif 'WheelMotor' in str(type(axis)):
            thismotor = axis
        else:                       # presume it's an xafs_XXXX motor
            thismotor = motor_nicknames[axis]

        current = thismotor.position
        if current+start < thismotor.limits[0]:
            error_msg(f'These scan parameters will take {thismotor.name} outside it\'s lower limit of {thismotor.limits[0]}')
            whisper(f'(starting position = {thismotor.position})')
            return(yield from null())
        if current+stop > thismotor.limits[1]:
            error_msg(f'These scan parameters will take {thismotor.name} outside it\'s upper limit of {thismotor.limits[1]}')
            whisper(f'(starting position = {thismotor.position})')
            return(yield from null())

        BMMuser.motor = thismotor

        # sanity checks on detector
        if detector not in ('It', 'If', 'I0', 'Iy', 'Pips', 'Ir', 'Both', 'Bicron', 'Ic0', 'Ic1', 'Xs', 'Xs1', 'Xs4', 'Xs7', 'Pilatus', 'Eiger', 'Dante'):
            error_msg('\n*** %s is not a linescan measurement (%s)\n' %
                      (detector, 'it, if, i0, iy, pips, ir, both, bicron, Ic0, Ic1, xs, xs1, xs4, xs7, pilatus, eiger, dante'))
            yield from null()
            return

        yield from mv(_locked_dwell_time, inttime)
        if detector == 'Xs':
            yield from mv(xs.cam.acquire_time, inttime)
            yield from mv(xs.total_points, nsteps)
        dets  = ION_CHAMBERS.copy()
        detname = ''

        # If should be xs when using Xspress3
        if with_xspress3 and detector == 'If':
            detector = 'Xs'
        
        if detector == 'It':
            detname = 'transmission'
        elif detector == 'I0a' and ic0 is not None:
            detname = 'I0a'
        elif detector == 'I0b' and ic0 is not None:
            detname = 'I0b'
        elif detector == 'Ir':
            detname = 'reference'
        elif detector == 'I0':
            detname = 'I0'
        elif detector == 'Bicron':
            dets.append(bicron)
            detname = 'Bicron'
        elif detector == 'Iy':
            denominator = ' / I0'
            detname = 'electron yield'
        elif detector == 'If':
            dets.append(xs)
            detname = 'fluorescence'
        elif detector == 'Xs':
            dets.append(xs)
            detname = 'fluorescence'
            yield from mv(xs.total_points, nsteps) # Xspress3 demands that this be set up front

        elif detector == 'Xs1':
            dets.append(xs1)
            detname = 'fluorescence'
            yield from mv(xs1.total_points, nsteps) # Xspress3 demands that this be set up front

        elif detector == 'Pilatus':
            dets.append(pilatus)
            detname = 'pilatus'
            pilatus.hdf5.stage_sigs['num_capture'] = nsteps  # pilatus demands that this be set up front

        elif detector == 'Eiger':
            dets.append(eiger)
            detname = 'eiger'
            eiger.hdf5.stage_sigs['num_capture'] = nsteps  # eiger demands that this be set up front

        elif detector == 'Dante':
            dets.append(dante)
            detname = 'dante'
            dante.hdf5.stage_sigs['num_capture'] = nsteps  # dante demands that this be set up front

            
        ## xs4 vs xs7
            
        elif detector == 'Ic0':
            pass
        elif detector == 'Ic1':
            dets.append(ic1)
            pass
            

        if 'PseudoSingle' in str(type(axis)):
            value = thismotor.readback.get()
        else:
            value = thismotor.user_readback.get()
        line1 = '%s, %s, %.3f, %.3f, %d -- starting at %.3f\n' % \
                (thismotor.name, detector, start, stop, nsteps, value)
        ##suspenders.set_suspenders()            # engage suspenders

        thismd = dict()
        thismd['XDI'] = dict()
        thismd['XDI']['Facility'] = dict()
        thismd['XDI']['Facility']['GUP'] = BMMuser.gup
        thismd['XDI']['Facility']['SAF'] = BMMuser.saf

        if 'BMM_kafka' not in md:
            md['BMM_kafka'] = dict()
        if 'hint' not in md['BMM_kafka'] or thismotor.name not in md['BMM_kafka']['hint']:
            md['BMM_kafka']['hint'] = f'linescan {detector} {thismotor.name}'
        fluo_detector = None
        if detector in ('Xs', 'Xs1', 'Fluorescence', 'Fluo', 'Flourescence', 'Flou'):
            fluo_detector = xs.name
        elif detector == 'Pips':
            fluo_detector = 'pips'
        elif detector == 'Dante':
            fluo_detector = 'Dante'
            
        kafka.message({'linescan': 'start',
                       'motor' : thismotor.name,
                       'detector' : detector,
                       'fluo_detector': fluo_detector,
                       'stack': stack})
            
        rkvs.set('BMM:scan:type',      'line')
        rkvs.set('BMM:scan:starttime', str(datetime.datetime.timestamp(datetime.datetime.now())))
        rkvs.set('BMM:scan:estimated', 0)

        def scan_xafs_motor(dets, motor, start, stop, nsteps):
            uid = yield from rel_scan(dets, motor, start, stop, nsteps, md={**thismd, **md, 'plan_name' : f'rel_scan linescan {motor.name} {detector}'})
            return uid

        thisuid = yield from scan_xafs_motor(dets, thismotor, start, stop, nsteps)
        kafka.message({'linescan': 'stop',})
        
        BMM_log_info(f'linescan: {line1}\tuid = {thisuid}')
        if dopluck is True:
            #action = input('\n' + bold_msg('Pluck motor position from the plot? ' + PROMPT))
            print()
            action = animated_prompt('Pluck motor position from the plot? ' + PROMPTNC)
            if action != '':
                if action[0].lower() == 'n' or action[0].lower() == 'q':
                    return(yield from null())
            yield from pluck(suggested_motor=thismotor)
            #yield from move_after_scan(thismotor)
            ## right here... put UID and plucked value in a store of some sort
            if user_ns["BMMuser"].mouse_click is not None:
                with open(os.path.join(WORKSPACE, 'logs', 'linescan_evaluation.txt'), 'a') as f:
                    f.write(f'''{now()}
     mode = {thismotor.name}/{detector}
     uid = {thisuid}
     position = {user_ns["BMMuser"].mouse_click}

''')

    
    def cleanup_plan():
        yield from resting_state_plan()


    ######################################################################
    # this is a tool for verifying a macro.  this replaces an xafs scan  #
    # with a sleep, allowing the user to easily map out motor motions in #
    # a macro                                                            #
    if BMMuser.macro_dryrun:
        info_msg('\nBMMuser.macro_dryrun is True.  Sleeping for %.1f seconds rather than running a line scan.\n' %
                 BMMuser.macro_sleep)
        countdown(BMMuser.macro_sleep)
        return(yield from null())
    ######################################################################
    thisuid = None
    user_ns['RE'].msg_hook = None
    yield from finalize_wrapper(main_plan(detector, axis, start, stop, nsteps, dopluck, force, stack, md), cleanup_plan())
    user_ns['RE'].msg_hook = BMM_msg_hook
    return thisuid


#############################################################
# extract a linescan from the database, write an ascii file #
#############################################################
def ls2dat(datafile, key):
    '''Export a linescan database entry to a simple column data file.

      ls2dat('myfile.dat', '0783ac3a-658b-44b0-bba5-ed4e0c4e7216')

    The arguments are a data file name and the database key.  The
    folder to which to write the file will be determined from the
    record's start document, so the file name is just the basename.

    '''
    #BMMuser, db = user_ns['BMMuser'], user_ns['db']
    kafka.message({'lsxdi': True, 'uid': key, 'filename': datafile})
    bold_msg('wrote linescan to %s' % datafile)
