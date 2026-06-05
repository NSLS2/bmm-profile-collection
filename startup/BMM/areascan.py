try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False

from bluesky.plans import grid_scan
from bluesky.callbacks import LiveGrid
from bluesky.plan_stubs import sleep, mv, mvr, null
from bluesky.preprocessors import subs_decorator, finalize_wrapper
import numpy, datetime
import os
import matplotlib.pyplot as plt
from ophyd.sim import noisy_det

from bmm_tools.tools.messages import *  # error_msg et al. + boxedtext

from BMM.user_ns.dwelltime import use_1element, use_4element, use_7element
from BMM.resting_state     import resting_state_plan
from BMM.user_ns.bmm       import kafka
from BMM.linescans         import motor_nicknames
from BMM.logging           import BMM_log_info, BMM_msg_hook, report
from BMM.functions         import plotting_mode
from BMM.workspace         import rkvs

from BMM.user_ns.base      import bmm_catalog
from BMM.user_ns.bmm       import BMMuser
from BMM.user_ns.dwelltime import _locked_dwell_time
from BMM.user_ns.detectors import quadem1, xs, xs1, xs4, xs7, ic0, ic1, ic2, ION_CHAMBERS
from BMM.user_ns.suspenders import suspenders

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)


def areascan(detector,
             slow, startslow, stopslow, nslow,
             fast, startfast, stopfast, nfast,
             pluck=True, force=False, dwell=0.1, fname=None,
             contour=True, log=False, md={}):
    '''
    Generic areascan plan.  This is a RELATIVE scan, relative to the
    current positions of the selected motors.

    For example:
       RE(areascan('it', 'x', -1, 1, 21, 'y', -0.5, 0.5, 11))

       detector: detector to display -- if, it, ir, or i0
       slow:      slow axis motor or nickname
       startslow: starting value for slow axis of a relative scan
       stopslow:  ending value for slow axis of a relative scan
       nslow:     number of steps in slow axis
       fast:      fast axis motor or nickname
       startfast: starting value for fast axis of a relative scan
       stopfast:  ending value for fast axis of a relative scan
       nfast:     number of steps in fast axis
       pluck:     optional flag for whether to offer to pluck & move motor
       force:     optional flag for forcing a scan even if not clear to start
       dwell:     dwell time at each point (0.1 sec default)
       contour:   True=plot with filled-in contours, False=plot with pcolormesh
       log:       True=plot log of signal in color scale
       md:        composable dictionary of metadata

    slow and fast are the BlueSky name for a motor (e.g. xafs_x)

    Plotting and file output are handled by kafka clients.
    '''

    def main_plan(detector,
                  slow, startslow, stopslow, nslow,
                  fast, startfast, stopfast, nfast,
                  pluck, force, dwell, fname, contour, log,  md):

        
        if force is True:
            (ok, text) = (True, '')
        else:
            (ok, text) = suspenders.clear_to_start()
            if force is False and ok is False:
                error_msg(text)
                BMMuser.final_log_entry = False
                yield from null()
                return

        user_ns['RE'].msg_hook = None

        ## sanity checks on slow axis
        if type(slow) is str: slow = slow.lower()
        if slow not in motor_nicknames.keys() and 'EpicsMotor' not in str(type(slow)) and 'PseudoSingle' not in str(type(slow)):
            error_msg('\n*** %s is not an areascan motor (%s)\n' %
                      (slow, str.join(', ', motor_nicknames.keys())))
            BMMuser.final_log_entry = False
            yield from null()
            return
        if slow in motor_nicknames.keys():
            slow = motor_nicknames[slow]

        current_slow = slow.position
        if current_slow+startslow < slow.limits[0]:
            error_msg(f'These scan parameters will take {slow.name} outside it\'s lower limit of {slow.limits[0]}')
            whisper(f'(starting position = {slow.position})')
            return(yield from null())
        if current_slow+stopslow > slow.limits[1]:
            error_msg(f'These scan parameters will take {slow.name} outside it\'s upper limit of {slow.limits[1]}')
            whisper(f'(starting position = {slow.position})')
            return(yield from null())


        ## sanity checks on fast axis
        if type(fast) is str: fast = fast.lower()
        if fast not in motor_nicknames.keys() and 'EpicsMotor' not in str(type(fast)) and 'PseudoSingle' not in str(type(fast)):
            error_msg('\n*** %s is not an areascan motor (%s)\n' %
                      (fast, str.join(', ', motor_nicknames.keys())))
            BMMuser.final_log_entry = False
            yield from null()
            return
        if fast in motor_nicknames.keys():
            fast = motor_nicknames[fast]

        current_fast = fast.position
        if current_fast+startfast < fast.limits[0]:
            error_msg(f'These scan parameters will take {fast.name} outside it\'s lower limit of {fast.limits[0]}')
            whisper(f'(starting position = {fast.position})')
            return(yield from null())
        if current_fast+stopfast > fast.limits[1]:
            error_msg(f'These scan parameters will take {fast.name} outside it\'s upper limit of {fast.limits[1]}')
            whisper(f'(starting position = {fast.position})')
            return(yield from null())
            

        detector = detector.capitalize()
        yield from mv(_locked_dwell_time, dwell)
        dets = ION_CHAMBERS.copy()

        if use_7element and detector == 'If':
            detector = 'Xs'
        elif use_4element and detector == 'If':
            detector = 'Xs'
        elif use_1element and detector == 'If':
            detector = 'Xs1'
            
        if detector == 'Xs':
            dets.append(xs)
        elif detector == 'It':
            detector = 'It'
        elif detector == 'Ir':
            detector = 'Ir'
        elif detector == 'I0':
            detector = 'I0'
        elif detector == 'Xs1':
            dets.append(xs1)
            yield from mv(xs.total_points, nslow*nfast)
        elif detector in ('Random', 'Noisy', 'Noisy_det'):
            dets.append(noisy_det)
            detector = 'noisy_det'
        
        line1 = f'slow motor: {slow.name}, {startslow}, {stopslow}, {nslow} -- starting at {slow.position:.3f}\n'
        line2 = f'fast motor: {fast.name}, {startfast}, {stopfast}, {nfast} -- starting at {fast.position:.3f}\n'

        npoints = nfast * nslow
        estimate = int(npoints*(dwell+0.43))
    
        kafka.close_plots()
    
        thismd = dict()
        thismd['XDI'] = dict()
        thismd['XDI']['Facility'] = dict()
        thismd['XDI']['Facility']['GUP'] = BMMuser.gup
        thismd['XDI']['Facility']['SAF'] = BMMuser.saf
        thismd['slow_motor'] = slow.name
        thismd['fast_motor'] = fast.name

        ini_f, ini_s = fast.position, slow.position
        report(f'Starting areascan at x,y = {fast.position:.3f}, {slow.position:.3f}', level='bold', slack=True)
        kafka.message({'mkdir': os.path.join(proposal_base(), 'maps')})
        kafka.message({'areascan'     : 'start',
                       'slow_motor'   : slow.name,
                       'slow_start'   : startslow,
                       'slow_stop'    : stopslow,
                       'slow_steps'   : nslow,
                       'slow_initial' : slow.position,
                       'fast_motor'   : fast.name,
                       'fast_start'   : startfast,
                       'fast_stop'    : stopfast,
                       'fast_steps'   : nfast,
                       'fast_initial' : fast.position,
                       'detector'     : detector,
                       'element'      : BMMuser.element,
                       'energy'       : user_ns['dcm'].energy.position})
        

        ## engage suspenders right before starting scan sequence
        if force is False: suspenders.set_suspenders()
    
        #@subs_decorator(areaplot)
        def make_areascan(dets,
                          slow, startslow, stopslow, nslow,
                          fast, startfast, stopfast, nfast,
                          fname, snake=False):
            BMMuser.final_log_entry = False
            
            uid = yield from grid_scan(dets,
                                       slow, startslow, stopslow, nslow,
                                       fast, startfast, stopfast, nfast,
                                       snake, md={**md, 'plan_name' : f'grid_scan measurement {slow.name} {fast.name} {detector}',
                                                  'BMM_kafka' : {'hint': f'areascan {detector.capitalize()} {slow.name} {fast.name} {contour} {log} {user_ns["dcm"].energy.position:.1f}',
                                                                 'pngout': fname}})
            yield from mv(slow, ini_s, fast, ini_f)  # return to starting position
            BMMuser.final_log_entry = True
            return uid

        rkvs.set('BMM:scan:type',      'area')
        rkvs.set('BMM:scan:starttime', str(datetime.datetime.timestamp(datetime.datetime.now())))
        rkvs.set('BMM:scan:estimated', estimate)
        
        BMM_log_info('begin areascan observing: %s\n%s%s' % (detector, line1, line2))
        asuid = yield from make_areascan(dets,
                                       slow, slow.position+startslow, slow.position+stopslow, nslow,
                                       fast, fast.position+startfast, fast.position+stopfast, nfast,
                                       fname, snake=False)
        kafka.message({'areascan': 'stop', 'uid' : asuid, 'filename': fname})
        report(f'map uid = {asuid}', level='bold', slack=True)

        # write .png, .mat, .xlsx with kafka here
        
    def cleanup_plan():
        print('Cleaning up after an area scan')
        db = user_ns['db']
        suspenders.clear_suspenders()
        if BMMuser.final_log_entry is True:
            if is_re_worker_active() is False:
                BMM_log_info('areascan finished\n\tuid = %s, scan_id = %d' % (bmm_catalog[-1].metadata['start']['uid'],
                                                                              bmm_catalog[-1].metadata['start']['scan_id']))
        yield from resting_state_plan()
        user_ns['RE'].msg_hook = BMM_msg_hook
        BMMuser.x, BMMuser.y, BMMuser.motor, BMMuser.motor2, BMMuser.fig, BMMuser.ax = [None] * 6

    ######################################################################
    # this is a tool for verifying a macro.  this replaces an xafs scan  #
    # with a sleep, allowing the user to easily map out motor motions in #
    # a macro                                                            #
    if BMMuser.macro_dryrun:
        info_msg('\nBMMuser.macro_dryrun is True.  Sleeping for %.1f seconds rather than running an area scan.\n' %
                       BMMuser.macro_sleep)
        countdown(BMMuser.macro_sleep)
        return(yield from null())
    ######################################################################
    BMMuser.final_log_entry = True
    user_ns['RE'].msg_hook = None

    yield from finalize_wrapper(main_plan(detector,
                                          slow, startslow, stopslow, nslow,
                                          fast, startfast, stopfast, nfast,
                                          pluck, force, dwell, fname, contour, log, md),
                                cleanup_plan())
    user_ns['RE'].msg_hook = BMM_msg_hook


def fetch_areaplot(uid=None, signal=None, log=False, contour=False):
    if uid is None:
        print('No uid provided.')
        return
    df = user_ns['db'].v2[uid]
    slow = df.metadata['start']['motors'][0]
    fast = df.metadata['start']['motors'][1]
    x = numpy.array(df.primary.read()[fast])
    y = numpy.array(df.primary.read()[slow])
    el = df.metadata['start']['plan_name'].split(" ")[-1][:-1]
    if signal is None:
        z = numpy.array(df.primary.read()[el+'1']) + numpy.array(df.primary.read()[el+'2']) + numpy.array(df.primary.read()[el+'3']) + numpy.array(df.primary.read()[el+'4'])
    elif signal.lower() in ('it', 'i0a', 'i0b', 'ita', 'itb'):
        z = numpy.array(df.primary.read()[signal.capitalize()])
    (nslow, nfast) = df.metadata['start']['shape']
    if log is True:
        z = numpy.log(z)
    
    
    fig = plt.figure(figsize=(nfast/20,nslow/20))
    plt.xlabel(f'fast axis ({fast}) position (mm)')
    plt.ylabel(f'slow axis ({slow}) position (mm)')
    plt.gca().invert_yaxis()  # plot an xafs_x/xafs_y plot upright
    if contour is True:
        plt.contourf(x[:nfast], y[::nfast], z.reshape(nslow, nfast), cmap=plt.cm.viridis)
    else:
        plt.pcolormesh(x[:nfast], y[::nfast], z.reshape(nslow, nfast), cmap=plt.cm.viridis)
    plt.colorbar()
    plt.show()


    
def as2dat(datafile, key):
    '''
    Export an areascan database entry to a simple column data file.

      as2dat('/path/to/myfile.dat', '42447313-46a5-42ef-bf8a-46fedc2c2bd1')

    or

      as2dat('/path/to/myfile.dat', 2948)

    The arguments are a data file name and the database key.
    '''
    pass
    #bold_msg('wrote areascan to %s' % datafile)
