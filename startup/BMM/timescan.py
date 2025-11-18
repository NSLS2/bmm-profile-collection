try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False

from bluesky.plans import count
from bluesky.callbacks import LiveGrid
from bluesky.plan_stubs import sleep, mv, mvr, null
from bluesky import __version__ as bluesky_version
from bluesky.preprocessors import subs_decorator, finalize_wrapper

import numpy
import os, datetime, re, textwrap, configparser, uuid
import pandas

import matplotlib
import matplotlib.pyplot as plt

from BMMCommon.tools.animated_prompt import PROMPTNC, animated_prompt

from BMM.dossier       import DossierTools
from BMM.functions     import countdown, boxedtext, now, isfloat, inflect, e2l, etok, ktoe, present_options, plotting_mode
from BMM.functions     import PROMPT
from BMM.functions     import error_msg, warning_msg, go_msg, url_msg, bold_msg, verbosebold_msg, list_msg, disconnected_msg, info_msg, whisper
from BMM.kafka         import kafka_message, file_exists
from BMM.logging       import BMM_log_info, BMM_msg_hook, report
from BMM.metadata      import bmm_metadata, display_XDI_metadata, metadata_at_this_moment
from BMM.resting_state import resting_state, resting_state_plan
from BMM.suspenders    import BMM_suspenders, BMM_clear_to_start, BMM_clear_suspenders
from BMM.xafs          import scan_metadata

from BMM.user_ns.base      import bmm_catalog
from BMM.user_ns.detectors import quadem1, ic0, ic1, ic2, xs, xs1, xs4, xs7, ION_CHAMBERS
from BMM.user_ns.dwelltime import _locked_dwell_time, use_7element, use_4element, use_1element

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)


####################################
# generic timescan vs. It/If/Ir/I0 #
####################################
def timescan(detector, readings, dwell, delay, outfile=None, force=False, md={}):
    '''
    Generic timescan plan.

    Parameters
    ----------
    detector : str
        detector to display -- if, it, ir, or i0
    readings : int
        number of measurements to make
    dwell : float
        dwell time in seconds for each measurement
    delay : float
        pause in seconds between measurements
    outfile :  str
        data file name (relative to proposal folder), False to not write
    force : bool
        flag for forcing a scan even if not clear to start

    This does not write an ASCII data file, but it does make a log entry.

    Use the ts2dat() function to extract the linescan from the
    database and write it to a file.

    Examples
       
    >>> RE(timescan('it', 100, 0.5))

    '''

    RE, BMMuser, dcm, db = user_ns['RE'], user_ns['BMMuser'], user_ns['dcm'], user_ns['db']
    rkvs = user_ns['rkvs']
    ######################################################################
    # this is a tool for verifying a macro.  this replaces an xafs scan  #
    # with a sleep, allowing the user to easily map out motor motions in #
    # a macro                                                            #
    if BMMuser.macro_dryrun:
        info_msg('\nBMMuser.macro_dryrun is True.  Sleeping for %.1f seconds rather than running a time scan.\n' %
                 BMMuser.macro_sleep)
        countdown(BMMuser.macro_sleep)
        return(yield from null())
    ######################################################################

    if force is False:
        (ok, text) = BMM_clear_to_start()
        if ok is False:
            error_msg(text)
            yield from null()
            return

    
    RE.msg_hook = None
    ## sanitize and sanity checks on detector
    detector = detector.capitalize()
    if detector not in ('It', 'If', 'I0', 'Iy', 'Ir', 'Ic1', 'Test', 'Transmission', 'Fluorescence', 'Flourescence'):
        error_msg(f'\n*** {detector} is not a timescan measurement (it, if, i0, iy, ir, ic1, transmission, fluorescence)\n')
        yield from null()
        return None

    yield from mv(_locked_dwell_time, dwell)
    dets  = ION_CHAMBERS.copy()

    if detector == 'Fluorescence' or detector == 'Flourescence':
        yield from mv(xs.cam.acquire_time, dwell)
        yield from mv(xs.total_points, readings)
    elif detector == 'Xs1':
        yield from mv(xs1.cam.acquire_time, dwell)
        yield from mv(xs1.total_points, readings)
    ## xs4 vs xs7

    line1 = '%s, N=%s, dwell=%.3f, delay=%.3f\n' % (detector, readings, dwell, delay)
    
    thismd = dict()
    thismd['XDI'] = dict()
    thismd['XDI']['Facility'] = dict()
    thismd['XDI']['Facility']['GUP']    = BMMuser.gup
    thismd['XDI']['Facility']['SAF']    = BMMuser.saf
    thismd['XDI']['Beamline'] = dict()
    thismd['XDI']['Beamline']['energy'] = dcm.energy.readback.get()
    thismd['XDI']['Scan'] = dict()
    thismd['XDI']['Scan']['dwell_time'] = dwell
    thismd['XDI']['Scan']['delay']      = delay
    thismd['XDI']['Scan']['element']    = BMMuser.element

    if 'BMM_kafka' not in md:
        md['BMM_kafka'] = dict()
    if 'hint' not in md['BMM_kafka']:
        md['BMM_kafka']['hint'] = f'timescan {detector}'
        
    def count_scan(dets, readings, delay, md):
        uid = yield from count(dets, num=readings, delay=delay, md={**thismd, **md, 'plan_name' : f'count measurement {detector}'})
        return uid
        
    rkvs.set('BMM:scan:type',      'time')
    rkvs.set('BMM:scan:starttime', str(datetime.datetime.timestamp(datetime.datetime.now())))
    rkvs.set('BMM:scan:estimated', 0)

    kafka_message({'timescan': 'start',
                   'detector' : detector,})

    
    uid = yield from count_scan(dets, readings, delay, md)

    kafka_message({'timescan': 'stop',
                   'fname' : outfile,
                   'uid' : uid, })
    
    BMM_log_info(f'timescan: {line1}\tuid = {uid}')

    yield from mv(_locked_dwell_time, 0.5)
    RE.msg_hook = BMM_msg_hook
    resting_state()
    return(uid)



def ts2dat(datafile, key):
    '''
    Export an timescan database entry to a simple column data file.

    Parameters
    ----------
    datafile : str
        name of output data file
    key : str
        UID of record in database

    Example
    -------
    >>> ts2dat('/path/to/myfile.dat', '42447313-46a5-42ef-bf8a-46fedc2c2bd1')
    '''
    kafka_message({'seadxdi': True, 'uid': key, 'filename': datafile})
    bold_msg('wrote timescan to %s' % datafile)


###############################################################################
# See                                                                         #
#   Single-energy x-ray absorption detection: a combined electronic and       #
#   structural local probe for phase transitions in condensed matter          #
#   A Filipponi, M Borowski, P W Loeffen, S De Panfilis, A Di Cicco,          #
#   F Sperandini, M Minicucci and M Giorgetti                                 #
#   Journal of Physics: Condensed Matter, Volume 10, Number 1                 #
#   http://iopscience.iop.org/article/10.1088/0953-8984/10/1/026/meta         #
###############################################################################
def sead(inifile=None, force=False, **kwargs):
    '''
    Read an INI file for scan matadata, then perform a single energy
    absorption detection measurement.

    '''
    def main_plan(inifile, force, **kwargs):


        # if force is False:
        #     (ok, ctstext) = BMM_clear_to_start()
        #     if ok is False:
        #         error_msg(ctstext)
        #         yield from null()
        #         return

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## read and check INI content205
        orig = inifile
        if not os.path.isfile(inifile):
            inifile = os.path.join(BMMuser.workspace, inifile)
            if not os.path.isfile(inifile):
                print(inifile)
                warning_msg('\n%s does not exist!  Bailing out....\n' % orig)
                return(orig, -1)
        bold_msg('reading ini file: %s' % inifile)
        (p, f) = scan_metadata(inifile=inifile, **kwargs)
        if not any(p):          # scan_metadata returned having printed an error message
            return(yield from null())
        #if not os.path.isdir(p['folder']):
        #    error_msg('\n%s is not a folder\n' % p['folder'])
        #    return(yield from null())

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## close the shutter if requested
        if p['shutter'] is True:
            yield from shb.close_plan()
        
        detector = 'It'
        if 'trans' in p['mode'].lower():
            detector = 'transmission'
        elif 'fluo' in p['mode'].lower() or 'flou' in p['mode'].lower():
            detector = 'fluorescence'
            
        elif 'test' in p['mode'].lower():
            detector = 'Test'


        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## verify output file name won't be overwritten
        outfile = f"{p['filename']}.{int(p['start']):03d}"
        if file_exists(filename=outfile, number=False):
            error_msg('%s already exists!  Bailing out....' % outfile)
            return(yield from null())

        kafka_message({'dossier': 'start', 'stub': p['filename']})
        rid = str(uuid.uuid4())[:8]
        report(f'== starting single energy absorption detection scan', level='bold', slack=True, rid=rid)

        
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## prompt user and verify that we are clear to start
        text = '\n'
        for k in ('folder', 'filename', 'energy', 'npoints', 'dwell', 'delay',
                  'sample', 'prep', 'comment', 'mode', 'shutter', 'snapshots'):  # , 'experimenters'
            text = text + '      %-13s : %-50s\n' % (k,p[k])
        ## NEVER prompt when using queue server
        if is_re_worker_active() is True:
            BMMuser.prompt = False
        if BMMuser.prompt:
            boxedtext(text + '\n      %-13s : %-50s\n' % ('output file',outfile), title='How does this look?', color='green')
            #action = input("\nBegin time scan? " + PROMPT)
            print()
            action = animated_prompt('Begin time scan? ' + PROMPTNC)
            if action != '':
                if action[0].lower() == 'n' or action[0].lower() == 'q':
                    return(yield from null())

        ## gather up input data into a format suitable for the dossier
        with open(inifile, 'r') as fd: content = fd.read()
        output = re.sub(r'\n+', '\n', re.sub(r'\#.*\n', '\n', content)) # remove comment and blank lines
        clargs = textwrap.fill(str(kwargs), width=50) # .replace('\n', '<br>')
        BMM_log_info('starting raster scan using %s:\n%s\ncommand line arguments = %s' % (inifile, output, str(kwargs)))

            
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        # organize metadata for injection into database and XDI output
        bold_msg('gathering metadata')
        md = bmm_metadata(measurement   = p['mode'],
                          experimenters = BMMuser.experimenters,
                          edge          = p['edge'],
                          element       = p['element'],
                          edge_energy   = p['energy'],
                          direction     = 0,
                          scantype      = 'fixed',
                          channelcut    = p['channelcut'],
                          mono          = 'Si(%s)' % dcm._crystal,
                          i0_gas        = 'N2', #\
                          it_gas        = 'N2', # > these three need to go into INI file
                          ir_gas        = 'N2', #/
                          sample        = p['sample'],
                          prep          = p['prep'],
                          stoichiometry = None,
                          mode          = p['mode'],
                          comment       = p['comment'],)
        md['Beamline']['energy'] = dcm.energy.position
        md['Scan']['dwell_time'] = p['dwell']
        md['Scan']['delay']      = p['delay']
        md['_kind'] = 'sead'

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## snap photos
        if p['snapshots']:
            yield from dossier.cameras(p['folder'], p['filename'], md)
            
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## capture dossier metadata for start document
        md['_snapshots'] = {**dossier.cameras_md}

        pngout = f"{p['filename']}_sead_{now()}.png"
        these_kwargs = {'start'     : p['start'],
                        'end'       : p['start']+p['nscans']-1,
                        'startdate' : BMMuser.date,
                        'bounds'    : ' '.join(map(str, p['bounds_given'])),
                        'steps'     : ' '.join(map(str, p['steps'])),
                        'times'     : ' '.join(map(str, p['times'])),
                        'pngfile'   : pngout,
        }
        ## for dossier
        with open(os.path.join(BMMuser.workspace, inifile)) as f:
            initext = ''.join(f.readlines())
        user_metadata = {**p, **these_kwargs, 'initext': initext, 'clargs': clargs, 'experimenters': BMMuser.experimenters}
        md['_user'] = user_metadata
        md['_filename'] = outfile

        
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## populate the static html page for this scan 
        these_kwargs = {'npoints': p['npoints'], 'dwell': p['dwell'], 'delay': p['delay'], 'shutter': p['shutter']}

        rightnow = metadata_at_this_moment()
        for family in rightnow.keys():       # transfer rightnow to md
            if type(rightnow[family]) is dict:
                if family not in md:
                    md[family] = dict()
                for k in rightnow[family].keys():
                    md[family][k] = rightnow[family][k]
        xdi = {'XDI': md}

        BMM_log_info('Starting single-energy absorption detection time scan using\n%s:\n%s\nCommand line arguments = %s\nMoving to measurement energy: %.1f eV' %
                     (inifile, text, str(kwargs), p['energy']))


        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## move to the energy specified in the INI file
        bold_msg('Moving to measurement energy: %.1f eV' % p['energy'])
        dcm.mode = 'fixed'
        yield from mv(dcm.energy, p['energy'])

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## open the shutters (which were closed at the start of sead)
        if p['shutter'] is True:
            yield from shb.open_plan()

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## engage suspenders right before starting measurement
        if not force: BMM_suspenders()

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## perform the actual time scan
        seaduid = yield from timescan(detector, p['npoints'], p['dwell'], p['delay'],
                                      outfile=pngout,
                                      force=force, md={**xdi})

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## close the shutters again
        if p['shutter'] is True:
            yield from shb.close_plan()

        kafka_message({'seadxdi': True, 'uid' : seaduid, 'filename': outfile})
        kafka_message({'dossier' : 'set',
                       'rid'     : rid,
                       'folder'  : BMMuser.folder,
                       'uidlist' : [seaduid,],
                       })
        kafka_message({'dossier' : 'sead', })
                   
    def cleanup_plan():
        print('Cleaning up after single energy absorption detection measurement')
        BMM_clear_suspenders()
        how = 'finished  :tada:'
        try:
            if 'primary' not in bmm_catalog[-1].metadata['stop']['num_events']:
                how = '*stopped*'
            elif bmm_catalog[-1].metadata['stop']['num_events']['primary'] != bmm_catalog[-1].metadata['start']['num_points']:
                how = '*stopped*'
        except:
            how = '*stopped*'
        if how != 'stopped':
            report(f'== SEAD scan {how}', level='bold', slack=True)
        else:
            whisper('Quitting SEAD scan.')

        yield from resting_state_plan()

    
    RE, dcm, BMMuser, db, shb = user_ns['RE'], user_ns['dcm'], user_ns['BMMuser'], user_ns['db'], user_ns['shb']
    openclose = False
    #if openclose is True:
    #    shb.close_plan()
    RE.msg_hook = None
    dossier = DossierTools()

    if is_re_worker_active():
        inifile = '/home/xf06bm/Data/bucket/sead.ini'
    if inifile is None:
        inifile = present_options('ini')
    if inifile is None:
        return(yield from null())
    if inifile[-4:] != '.ini':
        inifile = inifile+'.ini'
    yield from finalize_wrapper(main_plan(inifile, force, **kwargs), cleanup_plan())
    RE.msg_hook = BMM_msg_hook
        
