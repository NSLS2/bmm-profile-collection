from bluesky.plans import scan_nd, count
from bluesky.plan_stubs import sleep, mv, null
from bluesky.preprocessors import subs_decorator, finalize_wrapper
#from databroker.core import SingleRunCache

import numpy, os, re, shutil, uuid, time
import textwrap, configparser, datetime
from cycler import cycler
import matplotlib
import matplotlib.pyplot as plt

from PIL import Image
from tiled.client import from_profile

from urllib.parse import quote

from BMM.dossier         import DossierTools
from BMM.functions       import countdown, boxedtext, now, isfloat, inflect, e2l, etok, ktoe, present_options, plotting_mode
from BMM.functions       import PROMPT, DEFAULT_INI, proposal_base, PROMPTNC, animated_prompt
from BMM.functions       import error_msg, warning_msg, go_msg, url_msg, bold_msg, verbosebold_msg, list_msg, disconnected_msg, info_msg, whisper
from BMM.kafka           import kafka_message, close_plots, file_exists
from BMM.linescans       import rocking_curve
from BMM.logging         import BMM_log_info, BMM_msg_hook, report
from BMM.metadata        import bmm_metadata, display_XDI_metadata, metadata_at_this_moment
from BMM.modes           import get_mode, describe_mode
from BMM.motor_status    import motor_status
from BMM.periodictable   import edge_energy, Z_number, element_name
from BMM.resting_state   import resting_state_plan
from BMM.suspenders      import BMM_suspenders, BMM_clear_to_start, BMM_clear_suspenders
from BMM.xafs_functions  import conventional_grid, sanitize_step_scan_parameters

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from BMM.user_ns.base      import bmm_catalog, WORKSPACE
from BMM.user_ns.dwelltime import _locked_dwell_time, use_7element, use_4element, use_1element
from BMM.user_ns.detectors import quadem1, xs, xs1, xs4, xs7, ic0, ic1, ic2, pilatus, dante, ION_CHAMBERS

try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False






def next_index(folder=None, stub=None, maxtries=6, verbose=False):
    '''Find the next numeric filename extension for a filename stub in the
    specified folder in the proposals directory.

    This sends a message over kafka asking the file manager worker to
    search for the next index.  The worker posts the value to redis.
    This function sets that redis key to "None" and polls the
    appropriate redis key for a value interpretable integer.
   
    arguments
    =========
    folder: (str)
      folder in proposal directory to probe [proposal_base()]

    stub: (str)
      filename stub to check, i.e. filename without extension

    maxtries: (int)
      maximum number of attempts to read before giving up and returning None [6]

    verbose: (bool)
      if True, be noisy as we wait for a result

    '''
    if folder is None:
        folder = proposal_base()
    if stub is None:
        error_msg('No stub supplied to next_index')
        return(None)
    rkvs = user_ns['rkvs']
    rkvs.set('BMM:next_index', 'None')
    kafka_message({'next_index': True, 'folder': folder, 'stub': stub})
    time.sleep(1)
    answer = rkvs.get('BMM:next_index').decode('utf8')
    count = 0
    if verbose: print(f"{count = }, {answer = }")
    while answer == 'None':
        time.sleep(0.1*2**count)
        answer = rkvs.get('BMM:next_index').decode('utf8')
        count += 1
        if verbose: print(f"{count = }, {answer = }")
        if count > maxtries:
            return(None)
    return int(answer)




## need more error checking:
##   * k^2 times
##   * switch back to energy units after a k-valued boundary?
##   * pre-edge k-values steps & times


    


def scan_metadata(inifile=None, **kwargs):
    """Typical use is to specify an INI file, which contains all the
    metadata relevant to a set of scans.  This function is called with
    one argument:

      parameters = scan_metadata(inifile='/path/to/inifile')

      inifile:  fully resolved path to INI file describing the measurement.

    A dictionary of metadata is returned.

    As part of a multi-scan plan (i.e. a macro), individual metadata
    can be specified as kwargs to override values in the INI file.
    The kwarg keys are the same as the keys in the dictionary which is
    returned:

    Parameters
    ----------
    folder : str
        folder for saved XDI files
    filename : str
        filename stub for saved XDI files
    experimenters [str] 
        names of people involved in this measurements
    e0 : float
        edge energy, reference value for energy grid
    element : str
        one- or two-letter element symbol
    edge : str
        K, L3, L2, or L1
    sample : str
        description of sample, perhaps stoichiometry
    prep : str
        a short statement about sample preparation
    comment : str
        user-supplied comment about the data
    nscan : int
        number of repetitions
    start : int
        starting scan number, XDI file will be filename.###
    snapshots : bool
        True = capture analog and XAS cameras before scan sequence
    usbstick : bool
        True = munge filenames so they can be written to a VFAT USB stick
    rockingcurve  [bool] 
        True = measure rocking curve at pseudo channel cut energy
    lims : bool
        False = force both htmlpage and snapshot to be false
    htmlpage : bool
        True = capture dossier of a scan sequence as a static html page
    bothways : bool
        True = measure in both monochromator directions
    channelcut : bool
        True = measure in pseudo-channel-cut mode
    ththth : bool
        True = measure using the Si(333) reflection
    mode : str
        transmission, fluorescence, or reference -- how to display the data
    bounds : list
        scan grid boundaries (not kwarg-able at this time)
    steps : list
        scan grid step sizes (not kwarg-able at this time)
    times : list
        scan grid dwell times (not kwarg-able at this time)

    Any or all of these can be specified.  Values from the INI file
    are read first, then overridden with specified values.  If values
    are specified neither in the INI file nor in the function call,
    (possibly) sensible defaults are used.

    """
    #frame = inspect.currentframe()          # see https://stackoverflow.com/a/582206 and
    #args  = inspect.getargvalues(frame)[3]  # https://docs.python.org/3/library/inspect.html#inspect.getargvalues

    BMMuser, dcm = user_ns['BMMuser'], user_ns['dcm']
    parameters = dict()

    if inifile is None:
        error_msg('\nNo inifile specified\n')
        return {}, {}
    if not os.path.isfile(inifile):
        error_msg('\ninifile does not exist\n')
        return {}, {}

    config = configparser.ConfigParser(interpolation=None)
    config.read_file(open(inifile))

    found = dict()

    ## ----- scan regions (what about kwargs???)
    for a in ('bounds', 'steps', 'times'):
        found[a] = False
        parameters[a] = []
        if a not in kwargs:
            try:
                #for f in config.get('scan', a).split():
                for f in re.split('[ \t,]+', config.get('scan', a).strip()):
                    try:
                        parameters[a].append(float(f))
                    except:
                        parameters[a].append(f)
                    found[a] = True
            except:
                parameters[a] = getattr(BMMuser, a)
        else:
            this = str(kwargs[a])
            for f in this.split():
                try:
                    parameters[a].append(float(f))
                except:
                    parameters[a].append(f)
            found[a] = True
        parameters['bounds_given'] = parameters['bounds'].copy()

    (problem, text, reference) = sanitize_step_scan_parameters(parameters['bounds'], parameters['steps'], parameters['times'])
    if len(text) > 1:
        print(text)
        print(f'\nsee: {reference}')
    if problem:
        return {}, {}

    ## ----- strings
    for a in ('folder', 'element', 'edge', 'filename', 'comment',
              'mode', 'sample', 'prep', 'url', 'doi', 'cif'): # , 'experimenters'
        found[a] = False
        if a not in kwargs:
            try:
                parameters[a] = config.get('scan', a)
                found[a] = True
            except configparser.NoOptionError:
                parameters[a] = getattr(BMMuser, a)
        else:
            parameters[a] = str(kwargs[a])
            found[a] = True

    parameters['mode'] = parameters['mode'].lower()
    
    ## ----- start value
    if 'start' not in kwargs:
        try:
            parameters['start'] = str(config.get('scan', 'start'))
            found['start'] = True
        except configparser.NoOptionError:
            parameters[a] = getattr(BMMuser, a)
    else:
        parameters['start'] = str(kwargs['start'])
        found['start'] = True
    try:
        if parameters['start'] == 'next':
            parameters['start'] = next_index(stub=parameters['filename'])
        else:
            parameters['start'] = int(parameters['start'])
    except ValueError:
        error_msg('\nstart value must be a positive integer or "next"')
        parameters['start'] = -1
        found['start'] = False

    ## ----- integers
    for a in ('nscans', 'npoints'):
        found[a] = False
        if a not in kwargs:
            try:
                parameters[a] = int(config.get('scan', a))
                found[a] = True
            except configparser.NoOptionError:
                parameters[a] = getattr(BMMuser, a)
        else:
            parameters[a] = int(kwargs[a])
            found[a] = True

    ## ----- floats
    for a in ('e0', 'energy', 'inttime', 'dwell', 'delay'):
        found[a] = False
        if a not in kwargs:
            try:
                parameters[a] = float(config.get('scan', a))
                found[a] = True
            except configparser.NoOptionError:
                parameters[a] = getattr(BMMuser, a)
        else:
            parameters[a] = float(kwargs[a])
            found[a] = True

    ## ----- booleans
    for a in ('snapshots', 'htmlpage', 'lims', 'bothways', 'channelcut', 'usbstick', 'rockingcurve', 'ththth', 'shutter'):
        found[a] = False
        if a not in kwargs:
            try:
                parameters[a] = config.getboolean('scan', a)
                found[a] = True
            except configparser.NoOptionError:
                parameters[a] = getattr(BMMuser, a)
        else:
            parameters[a] = bool(kwargs[a])
            found[a] = True
    if parameters['lims'] is False:
        parameters['htmlpage'] = False
        parameters['snapshots'] = False
            
    if dcm._crystal != '111' and parameters['ththth']:
        error_msg('\nYou must be using the Si(111) crystal to make a Si(333) measurement\n')
        return {}, {}

    if not found['e0'] and found['element'] and found['edge']:
        parameters['e0'] = edge_energy(parameters['element'], parameters['edge'])
        if parameters['e0'] is None:
            error_msg('\nCannot figure out edge energy from element = %s and edge = %s\n' % (parameters['element'], parameters['edge']))
            return {}, {}
        else:
            found['e0'] = True
            #print('\nUsing tabulated value of %.1f for the %s %s edge\n' % (parameters['e0'], parameters['element'], parameters['edge']))
        if parameters['e0'] > 23500:
            error_msg('\nThe %s %s edge is at %.1f, which is ABOVE the measurement range for BMM\n' %
                      (parameters['element'], parameters['edge'], parameters['e0']))
            return {}, {}
        if parameters['e0'] < 4000:
            error_msg('\nThe %s %s edge is at %.1f, which is BELOW the measurement range for BMM\n' %
                      (parameters['element'], parameters['edge'], parameters['e0']))
            return {}, {}

            
    return parameters, found



def channelcut_energy(e0, bounds, ththth):
    '''From the scan parameters, find the energy at the center of the angular range of the scan.
    If the center of the angular range is too close to (or below) e0, use 50 eV above e0.
    '''
    dcm = user_ns['dcm']
    for i,s in enumerate(bounds):
        if type(s) is str:
            this = float(s[:-1])
            bounds[i] = ktoe(this)
    amin = dcm.e2a(e0+bounds[0])
    amax = dcm.e2a(e0+bounds[-1])
    if ththth:
        amin = dcm.e2a((e0+bounds[0])/3.0)
        amax = dcm.e2a((e0+bounds[-1])/3.0)
    aave = amin + 1.0*(amax - amin) / 2.0
    wavelength = dcm.wavelength(aave)
    eave = e2l(wavelength)
    if eave < e0 + 30:
        eave = e0 + 50
    return eave


def attain_energy_position(value):
    '''Attempt to move to an energy position, attempting to deal
    gracefully with encoder loss on the Bragg axis.

    Argument
    ========
      value : (float) target energy value

    Returns True for success, False for failure
    '''
    dcm, dcm_bragg = user_ns['dcm'], user_ns['dcm_bragg']
    BMMuser = user_ns['BMMuser']
    dcm_bragg.clear_encoder_loss()
    yield from mv(dcm.energy, value)
    count = 0
    while abs(dcm.energy.position - value) > 0.2 :
        if count > 4:
            error_msg('Unresolved encoder loss on Bragg axis.  Stopping XAFS scan.')
            BMMuser.final_log_entry = False
            yield from null()
            return False
        print('Clearing encoder loss and re-trying movement to pseudo-channel-cut energy...')
        dcm_bragg.clear_encoder_loss()
        yield from sleep(2)
        yield from mv(dcm.energy, value)
        count = count + 1
    return True


def ini_sanity(found):
    '''Very simple sanity checking of the scan control file.'''
    ok = True
    missing = []
    for a in ('bounds', 'steps', 'times', 'e0', 'element', 'edge', 'filename', 'nscans', 'start'):
        if found[a] is False:
            ok = False
            missing.append(a)
    return (ok, missing)

def mono_sanity():
    '''Verify that the physical position of the mono matches its
    configuration.

    '''
    dcm   = user_ns['dcm']
    dcm_x = user_ns['dcm_x']
    msg, isok = '', True
    if '311' in dcm._crystal and dcm_x.user_readback.get() < 10:
        BMMuser.final_log_entry = False
        msg = 'The DCM is in the 111 position, configured as 311'
        isok = False
    if '111' in dcm._crystal and dcm_x.user_readback.get() > 10:
        BMMuser.final_log_entry = False
        msg = 'The DCM is in the 311 position, configured as 111'
        isok = False
    if isok is False:
        msg += '\n\tdcm.x: %.2f mm\t dcm._crystal: %s' % (dcm_x.user_readback.get(), dcm._crystal)
    return(isok, msg)



##########################################################
# --- export a database energy scan entry to an XDI file #
##########################################################
def xas2xdi(datafile, key):
    '''
    Export a database entry for an XAFS scan to an XDI file.

    Parameters
    ----------
    datafile : str
        output file name
    key : str
        UID in database


    Examples
    --------
    >>> xas2xdi('/path/to/myfile.xdi', '0783ac3a-658b-44b0-bba5-ed4e0c4e7216')

    '''
    kafka_message({'xasxdi': True, 'uid' : key, 'filename': datafile})
    bold_msg('wrote %s' % dfile)



#########################
# -- the main XAFS scan #
#########################
def xafs(inifile=None, **kwargs):
    '''
    Read an INI file for scan matadata, then perform an XAFS scan sequence.
    '''
    def main_plan(inifile, **kwargs):

        ## verify mono position and configuration are consistent
        isok, msg = mono_sanity()
        if isok is False:
            error_msg(msg)
            yield from null()
            return
        
        verbose = False
        if 'verbose' in kwargs and kwargs['verbose'] is True:
            verbose = True
            
        supplied_metadata = dict()
        if 'md' in kwargs and type(kwargs['md']) == dict:
            supplied_metadata = kwargs['md']

        if is_re_worker_active():
            BMMuser.prompt = False
            kwargs['force'] = True

        ## verify that it is OK to start the scan
        if verbose: verbosebold_msg('checking clear to start (unless force=True)')
        if 'force' in kwargs and kwargs['force'] is True:
            (ok, text) = (True, '')
        else:
            (ok, text) = BMM_clear_to_start()
            if ok is False:
                BMMuser.final_log_entry = False
                error_msg('\n'+text)
                bold_msg('Not clear to start scan sequence....\n')
                yield from null()
                return
        #_locked_dwell_time.quadem_dwell_time.settle_time = 0


        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## decide whether to ask kafka worker to copy INI file to proposal folder
        ## default is to copy the INI file, automation will usually not copy it
        ## thus the user's INI file will get copied unless they explicitly ask that it not be
        if 'copy' in kwargs:
            if kwargs['copy'] is True:
                copy_ini = True
            else:
                copy_ini = False
        else:
            copy_ini = True

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## user input, find and parse the INI file
        if verbose: verbosebold_msg('time estimate')
        inifile, estimate = howlong(inifile, interactive=False, **kwargs)
        if estimate == -1:
            BMMuser.final_log_entry = False
            yield from null()
            return
        (p, f) = scan_metadata(inifile=inifile, **kwargs)
        p['channelcut'] = True
        if p['lims'] is False:
            BMMuser.lims = False
        else:
            BMMuser.lims = True
        if not any(p):          # scan_metadata returned having printed an error message
            return(yield from null())

        
        
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## if in xs mode, make sure we are configured correctly
        if plotting_mode(p['mode']) == 'fluorescence' and use_7element is True:
            if (any(getattr(BMMuser, x) is None for x in ('element', 'xs1', 'xs2', 'xs3', 'xs4',  'xs5', 'xs6', 'xs7', 
                                                          'xschannel1', 'xschannel2', 'xschannel3', 'xschannel4',
                                                          'xschannel5', 'xschannel6', 'xschannel7'))):
                error_msg('BMMuser is not configured to measure correctly with the Xspress3 and the 4-element detector')
                error_msg('Likely solution:')
                error_msg('Set element symbol:  BMMuser.element = Fe  # (or whatever...)')
                error_msg('then do:             xs.measure_roi()')
                return(yield from null())
        if plotting_mode(p['mode']) == 'fluorescence' and use_4element is True:
            if (any(getattr(BMMuser, x) is None for x in ('element', 'xs1', 'xs2', 'xs3', 'xs4',
                                                          'xschannel1', 'xschannel2', 'xschannel3', 'xschannel4'))):
                error_msg('BMMuser is not configured to measure correctly with the Xspress3 and the 4-element detector')
                error_msg('Likely solution:')
                error_msg('Set element symbol:  BMMuser.element = Fe  # (or whatever...)')
                error_msg('then do:             xs.measure_roi()')
                return(yield from null())
        if plotting_mode(p['mode']) == 'fluorescence' and use_1element is True:
            if (any(getattr(BMMuser, x) is None for x in ('element', 'xs8', 'xschannel8'))):
                error_msg('BMMuser is not configured to measure correctly with the Xspress3 and the 1-element detector')
                error_msg('Likely solution:')
                error_msg('Set element symbol:  BMMuser.element = Fe  # (or whatever...)')
                error_msg('then do:             xs.measure_roi()')
                return(yield from null())

        sub_dict = {'*' : '_STAR_',
                    '/' : '_SLASH_',
                    '\\': '_BACKSLASH_',
                    '?' : '_QM_',
                    '%' : '_PERCENT_',
                    ':' : '_COLON_',
                    '|' : '_VERBAR_',
                    '"' : '_QUOTE_',
                    '<' : '_LT_',
                    '>' : '_GT_',
                }
        vfatify = lambda m: sub_dict[m.group()]
        if p['usbstick']:
            new_filename = re.sub(r'[*:?"<>|/\\]', vfatify, p['filename'])
            if new_filename != p['filename']: 
                report('\nChanging filename from "%s" to %s"' % (p['filename'], new_filename), 'error')
                error_msg('\nThese characters cannot be in file names copied onto most memory sticks:')
                error_msg('\n\t* : ? % " < > | / \\')
                error_msg('\nSee ')+url_msg('https://en.wikipedia.org/wiki/Filename#Reserved_characters_and_words')
                p['filename'] = new_filename

            ## 255 character limit for filenames on VFAT
            # if len(p['filename']) > 250:
            #     BMMuser.final_log_entry = False
            #     error_msg('\nYour filename is too long,')
            #     error_msg('\nFilenames longer than 255 characters cannot be copied onto most memory sticks,')
            #     yield from null()
            #     return


        ## sometimes the kafka-redis-while loop in next_index() fails, give it another go
        nicount = 0
        if p['start'] is None:
            p['start'] = next_index(stub=p['filename'])
            if p['start'] is None:
                p['start'] = 1
                report(":bangbang: could not figure out starting index, setting to 1 :shrug:", slack=True)
                
        ## probably not necessary....
        if p['nscans'] is None:
            if 'foil' in p['filename']:
                p['nscans'] = 1
            else:
                p['nscans'] = 2
            report(f":bangbang: p['nscans'] found as None, setting to {p['nscans']}", slack=True)


        
        bail = False
        cnt = 0
        #for i in range(p['start'], p['start']+p['nscans'], 1):
        cnt += 1
        fname = p['filename']
        if p['usbstick']:
            fname = re.sub(r'[*:?"<>|/\\]', vfatify, fname)
        datafile = os.path.join(p['folder'], fname)
        if file_exists(filename=fname, start=p['start'], stop=p['start']+p['nscans']):
            report(f'{datafile} in range {p["start"]} - {p["start"]+p["nscans"]}: file name conflict!', 'error')
            report('\nOne or more output files already exist!  Quitting scan sequence....\n', 'error')
            BMMuser.final_log_entry = False
            yield from null()
            return

            
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## user verification (disabled by BMMuser.prompt)
        if verbose: verbosebold_msg('computing pseudo-channelcut energy')
        eave = channelcut_energy(p['e0'], p['bounds'], p['ththth'])
        if BMMuser.prompt:
            BMMuser.instrument = ''  # we are NOT using a spreadsheet, so unset instrument
            text = '\n'
            for k in ('bounds', 'bounds_given', 'steps', 'times'):
                addition = '      %-13s : %-50s\n' % (k,p[k])
                text = text + addition.rstrip() + '\n'
            for (k,v) in p.items():
                if k in ('bounds', 'bounds_given', 'steps', 'times'):
                    continue
                if k in ('npoints', 'dwell', 'delay', 'inttime', 'channelcut', 'bothways'):
                    continue
                if k in ('url', 'doi', 'cif'):
                    continue
                addition = '      %-13s : %-50s\n' % (k,v)
                text = text + addition.rstrip() + '\n'
            for k in ('post_webcam', 'post_anacam', 'post_usbcam1', 'post_usbcam2', 'post_xrf'):
                addition = '      %-13s : %-50s\n' % (k,getattr(user_ns['BMMuser'], k))
                text = text + addition.rstrip() + '\n'
            boxedtext(text, title='How does this look?', color='green')

            outfile = os.path.join(p['folder'], "%s.%3.3d" % (p['filename'], p['start']))
            print('\nFirst data file to be written to "%s"' % outfile)

            print(estimate)

            if not dcm.suppress_channel_cut:
                if p['ththth']:
                    print(f'\nSi(111) pseudo-channel-cut energy = {eave:1f} ; {eave*3:1f} on the Si(333)')
                else:
                    print(f'\nPseudo-channel-cut energy = {eave:1f}')

            #action = input("\nBegin scan sequence? " + PROMPT)
            print()
            action = animated_prompt('Begin scan sequence? ' + PROMPTNC)
            if action != '':
                if action[0].lower() == 'n' or action[0].lower() == 'q':
                    BMMuser.final_log_entry = False
                    yield from null()
                    return

        
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## gather up input data into a format suitable for the dossier
        with open(inifile, 'r') as fd: content = fd.read()
        output = re.sub(r'\n+', '\n', re.sub(r'\#.*\n', '\n', content)) # remove comment and blank lines
        clargs = textwrap.fill(str(kwargs), width=50) # .replace('\n', '<br>')
        BMM_log_info('starting XAFS scan using %s:\n%s\ncommand line arguments = %s' % (inifile, output, str(kwargs)))
        #BMM_log_info(motor_status())

        ## perhaps enter pseudo-channel-cut mode
        if p['rockingcurve']:
            report(f'running rocking curve at pseudo-channel-cut energy {eave:1f} eV', 'bold')
            yield from attain_energy_position(eave)
            yield from rocking_curve()
            kafka_message({'close': 'last'})
            RE.msg_hook = None
        if p['channelcut'] is True:
            yield from attain_energy_position(eave)
            report(f'entering pseudo-channel-cut mode at {eave:1f} eV', 'bold')
            dcm.mode = 'channelcut'
        else:
            dcm.mode = 'fixed'


        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## organize metadata for injection into database and XDI output
        bold_msg('gathering metadata')
        md = bmm_metadata(measurement   = p['mode'],
                          experimenters = BMMuser.experimenters,
                          edge          = p['edge'],
                          element       = p['element'],
                          edge_energy   = p['e0'],
                          direction     = 1,
                          scantype      = 'step',
                          channelcut    = True, # p['channelcut'],  ???
                          mono          = f'Si({dcm._crystal})',
                          i0_gas        = 'N2', #\
                          it_gas        = 'N2', # > these three need to go into INI file
                          ir_gas        = 'N2', #/
                          sample        = p['sample'],
                          prep          = p['prep'],
                          stoichiometry = None,
                          mode          = p['mode'],
                          comment       = p['comment'],
                          ththth        = p['ththth'],
        )

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## measure XRF spectrum at Eave
        if plotting_mode(p['mode']) in ('fluorescence', 'yield', 'pilatus', 'dante') and BMMuser.lims is True:
            print('capturing XRF for "xs" mode')
            yield from dossier.capture_xrf(p['filename'], plotting_mode(p['mode']), md)

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## snap photos
        if p['snapshots']:
            yield from dossier.cameras(p['folder'], p['filename'], md)

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## capture dossier metadata for start document
        md['_snapshots'] = {**dossier.xrf_md, **dossier.cameras_md}
            
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## this dictionary is used to populate the static html page for this scan sequence
        # see https://stackoverflow.com/a/5445983 for list of string idiom
        these_kwargs = {'start'     : p['start'],
                        'end'       : p['start']+p['nscans']-1,
                        'pccenergy' : eave,
                        'startdate' : BMMuser.date,
                        'bounds'    : ' '.join(map(str, p['bounds_given'])),
                        'steps'     : ' '.join(map(str, p['steps'])),
                        'times'     : ' '.join(map(str, p['times'])), }

        with open(os.path.join(BMMuser.workspace, inifile)) as f:
            initext = ''.join(f.readlines())
        user_metadata = {**p, **these_kwargs, 'initext': initext, 'clargs': clargs}
        md['_user'] = user_metadata
        basename = md['_user']['filename']
        rkvs.set('BMM:dossier:basename', basename)
        
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## set up a plotting subscription, anonymous functions for plotting various forms of XAFS

        # switch between old ion chambers with QuadEM and new self-contained ICs
        i0 = 'I0' # 'I0a' or 'I0b'
        it = 'It' # 'Ita' or 'Itb'
        ir = 'Ir' # 'Ira' or 'Irb'

        if 'yield' in p['mode']:
            quadem1.Iy.kind = 'hinted'
        if 'xs1' in p['mode']:
            yield from mv(xs1.cam.acquire_time, 0.5)
        elif 'fluo' in p['mode'] or 'flou' in p['mode'] or 'xs' in p['mode'] or 'yield' in p['mode']:
            yield from mv(xs.cam.acquire_time, 0.5)
        ## xs4 vs xs7
            

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## copy INI file to proposal folder
        if copy_ini is True:
            kafka_message({'copy' : True, 'file': inifile, 'target': proposal_base()})


        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## engage suspenders right before starting scan sequence
        if 'force' in kwargs and kwargs['force'] is True:
            pass
        else:
            BMM_suspenders()

        def scan_sequence(clargs): #, noreturn=False):
            ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
            ## compute energy and dwell grids
            bold_msg('computing energy and dwell time grids')
            (energy_grid, time_grid, approx_time, delta) = conventional_grid(p['bounds'], p['steps'], p['times'], e0=p['e0'], element=p['element'], edge=p['edge'], ththth=p['ththth'])


            ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
            ## make sure XSpress3 IOC knows how many data points to measure
            if plotting_mode(p['mode']) in ('fluorescence', 'yield', 'pilatus'):
                yield from mv(xs.total_points, len(energy_grid))
            ## ****  same for Dante ****

                
            ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
            ## sanity checks
            if energy_grid is None or time_grid is None or approx_time is None:
                error_msg('Cannot interpret scan grid parameters!  Bailing out....')
                BMMuser.final_log_entry = False
                yield from null()
                return
            BMMuser.element = rkvs.get('BMM:user:element').decode('utf-8')
            BMMuser.edge    = rkvs.get('BMM:user:edge').decode('utf-8')
            if p['element'] != BMMuser.element or p['edge'] != BMMuser.edge:
                error_msg(f'The photon delivery system is not configured for the {p["element"]} {p["edge"]} edge.  You need to run the "change_edge()" command.  Bailing out....')
                BMMuser.final_log_entry = False
                yield from null()
                return
            if any(t > 20 for t in time_grid):
                error_msg('Your scan asks for an integration time greater than 20 seconds, which the ion chamber electrometer cannot accommodate.  Bailing out....')
                BMMuser.final_log_entry = False
                yield from null()
                return
            if any(y > 23500 for y in energy_grid):
                error_msg('Your scan goes above 23500 eV, the maximum energy available at BMM.  Bailing out....')
                BMMuser.final_log_entry = False
                yield from null()
                return
            if dcm._crystal == '111' and any(y > 21200 for y in energy_grid):
                error_msg('Your scan goes above 21200 eV, the maximum energy value on the Si(111) mono.  Bailing out....')
                BMMuser.final_log_entry = False
                yield from null()
                return
            if dcm._crystal == '111' and any(y < 2900 for y in energy_grid): # IS THIS CORRECT???
                error_msg('Your scan goes below 2900 eV, the minimum energy value on the Si(111) mono.  Bailing out....')
                BMMuser.final_log_entry = False
                yield from null()
                return
            if dcm._crystal == '311' and any(y < 5500 for y in energy_grid):
                error_msg('Your scan goes below 5500 eV, the minimum energy value on the Si(311) mono.  Bailing out....')
                BMMuser.final_log_entry = False
                yield from null()
                return

            ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
            ## show the metadata to the user
            #display_XDI_metadata(md)
                
            ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
            ## store data in redis, used by cadashboard
            rkvs.set('BMM:scan:type',      'xafs')
            rkvs.set('BMM:scan:starttime', str(datetime.datetime.timestamp(datetime.datetime.now())))
            rkvs.set('BMM:scan:estimated', (approx_time * int(p['nscans']) * 60))
                
            ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
            ## loop over scan count
            close_plots()
            rid = str(uuid.uuid4())[:8]
            kafka_message({'dossier': 'start', 'stub': p['filename']})
            kafka_message({'dossier': 'set',
                           'folder' : BMMuser.folder,
                           'rid'    : rid})
            report(f'"{p["filename"]}", {p["element"]} {p["edge"]} edge, {inflect("scans", p["nscans"])}',
                   level='bold', slack=True, rid=rid)
            cnt = 0
            if any('quadem' in x.name for x in ION_CHAMBERS):
                with_yield = True
            else:
                with_yield = False
            kafka_message({'xafs_sequence' : 'start',
                           'element'       : p["element"],
                           'edge'          : p["edge"],
                           'folder'        : BMMuser.folder,
                           'workspace'     : BMMuser.workspace,
                           'repetitions'   : p["nscans"],
                           'mode'          : p['mode'],
                           'with_yield'    : with_yield })
            refmat = 'none'
            if p["element"] in user_ns['xafs_ref'].mapping:
                refmat = user_ns['xafs_ref'].mapping[p["element"]][3]
            sample = p['sample']
            if len(sample) > 50:
                sample = sample[:45] + ' ...'

            fluo_detector = None
            if plotting_mode(p['mode']) in ('fluorescence', 'pilatus'):
                fluo_detector = xs.name
            elif plotting_mode(p['mode']) == 'dante':
                fluo_detector = 'dante'
            kafka_message({'xafsscan': 'start',
                           'element': p["element"],
                           'edge': p["edge"],
                           'mode': plotting_mode(p['mode']),
                           'filename': p["filename"],
                           'repetitions': p["nscans"],
                           'sample': sample,
                           'reference_material': refmat,
                           'fluo_detector': fluo_detector})
            
            for i in range(p['start'], p['start']+p['nscans'], 1):
                cnt += 1
                fname = "%s.%3.3d" % (p['filename'], i)
                datafile = os.path.join(p['folder'], fname)
                if file_exists(filename=fname, start=i, stop=i):
                    ## shouldn't be able to get here, unless a file
                    ## was written since the scan sequence began....
                    report('%s already exists! (How did that happen?) Bailing out....' % (datafile), 'error')
                    yield from null()
                    return

                
                ## this block is in the wrong place.  should be outside the loop over repetitions
                ## same is true of several more things below
                slotno, ring, this_instrument = '', '', ''
                if 'wheel' in BMMuser.instrument.lower():
                    slotno = f', slot {xafs_wheel.current_slot()}'
                    ring = f' {xafs_wheel.slot_ring()} ring'
                    this_instrument = xafs_wheel.dossier_entry();
                elif 'glancing angle' in BMMuser.instrument.lower():
                    slotno = f', spinner {ga.current()}'
                    this_instrument = ga.dossier_entry();
                    md['_snapshots']['ga_filename'] = os.path.basename(ga.alignment_filename)
                    md['_snapshots']['ga_yuid']     = ga.y_uid
                    md['_snapshots']['ga_pitchuid'] = ga.pitch_uid
                    md['_snapshots']['ga_fuid']     = ga.f_uid
                elif 'lakeshore' in BMMuser.instrument.lower():
                    slotno = f', temperature {lakeshore.readback.get():.1f}'
                    this_instrument = lakeshore.dossier_entry();
                elif 'linkam' in BMMuser.instrument.lower():
                    slotno = f', temperature {linkam.readback.get():.1f}'
                    this_instrument = linkam.dossier_entry();
                # this one is a bit different, get dossier entry from gmb object,
                # there is no grid object....
                elif 'grid' in BMMuser.instrument.lower():
                    slotno = f', motor grid {gmb.motor1.name}, {gmb.motor2.name} = {gmb.position1:.1f}, {gmb.position2:.1f}'
                    this_instrument = gmb.dossier_entry();
                elif 'reflectivity' in BMMuser.instrument.lower():
                    this_iqnstrument = refl.dossier_entry();
                    
                    
                    
                report(f'starting repetition {cnt} of {p["nscans"]} -- {fname} -- {len(energy_grid)} energy points{slotno}{ring}', level='bold', slack=True)
                md['_filename'] = fname
                md['_user']['instrument'] = this_instrument

                ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
                ## compute trajectory
                energy_trajectory    = cycler(dcm.energy, energy_grid)
                dwelltime_trajectory = cycler(dwell_time, time_grid)

                ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
                ## need to set certain metadata items on a per-scan basis... temperatures, ring stats
                ## mono direction, ... things that can change during or between scan sequences
                
                md['Mono']['direction'] = 'forward'
                if p['bothways'] and cnt%2 == 0:
                    energy_trajectory    = cycler(dcm.energy, energy_grid[::-1])
                    dwelltime_trajectory = cycler(dwell_time, time_grid[::-1])
                    md['Mono']['direction'] = 'backward'
                    yield from attain_energy_position(energy_grid[-1]+5)
                    #dcm_bragg.clear_encoder_loss()
                    #yield from mv(dcm.energy, energy_grid[-1]+5)
                else:
                    ## if not measuring in both direction, lower acceleration of the mono
                    ## for the rewind, explicitly rewind, then reset for measurement
                    yield from mv(dcm_bragg.acceleration, BMMuser.acc_slow)
                    whisper('  Rewinding DCM to %.1f eV with acceleration time = %.2f sec' % (energy_grid[0]-5, dcm_bragg.acceleration.get()))
                    yield from attain_energy_position(energy_grid[0]-5)
                    #dcm_bragg.clear_encoder_loss()
                    #yield from mv(dcm.energy, energy_grid[0]-5)
                    yield from mv(dcm_bragg.acceleration, BMMuser.acc_fast)
                    whisper('  Resetting DCM acceleration time to %.2f sec' % dcm_bragg.acceleration.get())


                if plotting_mode(p['mode']) in ('fluorescence', 'yield', 'pilatus'):
                    #yield from mv(xs.cam.acquire_time, time_grid[0])
                    #yield from mv(xs.Acquire, 1)
                    yield from mv(xs.spectra_per_point, 1) 
                    yield from mv(xs.total_points, len(energy_grid))
                    hdf5_uid = xs.hdf5.file_name.value
                if p['mode'] == 'pilatus':
                    ## the next line does not work.  gets overridden by stage_sigs when staged :(
                    #yield from mv(pilatus.hdf5.num_capture, len(energy_grid))
                    ## this seems ugly and too far in the weeds, but it works
                    pilatus.hdf5.stage_sigs['num_capture'] = len(energy_grid)
                if p['mode'] == 'dante':
                    dante.hdf5.stage_sigs['num_capture'] = len(energy_grid)
                
                rightnow = metadata_at_this_moment() # see metadata.py
                for family in rightnow.keys():       # transfer rightnow to md
                    if type(rightnow[family]) is dict:
                        if family not in md:
                            md[family] = dict()
                        for k in rightnow[family].keys():
                            md[family][k] = rightnow[family][k]
                
                md['_kind'] = 'xafs'
                md['_pccenergy'] = round(eave, 3)

                if p['ththth']:
                    md['_kind'] = '333'
                which_detector = rkvs.get('BMM:xspress3').decode('utf-8')
                if which_detector == '1':
                    md['_dtc'] = (BMMuser.xs8,)
                elif which_detector == '7':
                    md['_dtc'] = (BMMuser.xs1, BMMuser.xs2, BMMuser.xs3, BMMuser.xs4, BMMuser.xs5, BMMuser.xs6, BMMuser.xs7)
                elif which_detector == '4':
                    md['_dtc'] = (BMMuser.xs1, BMMuser.xs2, BMMuser.xs3, BMMuser.xs4)
                else:
                    md['_dtc'] = (BMMuser.xs1, BMMuser.xs2, BMMuser.xs3, BMMuser.xs4, BMMuser.xs5, BMMuser.xs6, BMMuser.xs7)

                
                ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
                ## metadata for XDI entry in start document
                xdi = {'XDI': md}
                
                ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
                ## call the stock scan_nd plan with the correct detectors
                uid = None
                more_kafka = {'filename': p["filename"],
                              'folder': BMMuser.folder,
                              'element': p["element"],
                              'edge': p["edge"],
                              'repetitions': p["nscans"],
                              'count': cnt, }
                kafka_message({'xafsscan': 'next',
                               'count': cnt })
                if any(md in p['mode'] for md in ('trans', 'ref', 'test')):
                    uid = yield from scan_nd([*ION_CHAMBERS], energy_trajectory + dwelltime_trajectory,
                                             md={**xdi, **supplied_metadata, 'plan_name' : f'scan_nd xafs {p["mode"]}',
                                                 'BMM_kafka': { 'hint': f'xafs {p["mode"]}', **more_kafka }})

                elif plotting_mode(p['mode']) == 'fluorescence':
                    uid = yield from scan_nd([*ION_CHAMBERS, xs], energy_trajectory + dwelltime_trajectory,
                                             md={**xdi, **supplied_metadata, 'plan_name' : 'scan_nd xafs fluorescence',
                                                 'BMM_kafka': { 'hint':  'xafs xs', **more_kafka }})
                elif plotting_mode(p['mode']) == 'dante':
                    uid = yield from scan_nd([*ION_CHAMBERS, dante], energy_trajectory + dwelltime_trajectory,
                                             md={**xdi, **supplied_metadata, 'plan_name' : 'scan_nd xafs fluorescence dante',
                                                 'BMM_kafka': { 'hint':  'xafs xs', **more_kafka }})
                #elif plotting_mode(p['mode']) == 'xs1':
                #    uid = yield from scan_nd([*ION_CHAMBERS, xs1], energy_trajectory + dwelltime_trajectory,
                #                             md={**xdi, **supplied_metadata, 'plan_name' : 'scan_nd xafs fluorescence',
                #                                 'BMM_kafka': { 'hint':  'xafs xs1', **more_kafka }})
                elif plotting_mode(p['mode']) == 'yield':
                    uid = yield from scan_nd([*ION_CHAMBERS, xs], energy_trajectory + dwelltime_trajectory,
                                             md={**xdi, **supplied_metadata, 'plan_name' : 'scan_nd xafs yield + fluorescence',
                                                 'BMM_kafka': { 'hint':  'xafs yield', **more_kafka }})
                elif plotting_mode(p['mode']) == 'pilatus':
                    uid = yield from scan_nd([*ION_CHAMBERS, xs, pilatus], energy_trajectory + dwelltime_trajectory,
                                             md={**xdi, **supplied_metadata, 'plan_name' : 'scan_nd xafs fluorescence + pilatus',
                                                 'BMM_kafka': { 'hint':  'xafs fluo+pilatus', **more_kafka }})
                else:
                    error_msg('No valid plotting mode provided!')

                kafka_message({'xafs_sequence'      :'add',
                               'uid'                : uid})
                
                if plotting_mode(p['mode']) in ('fluorescence', 'yield', 'pilatus'):  # dante???
                    hdf5_uid = xs.hdf5.file_name.value
                    
                uidlist.append(uid)
                kafka_message({'xasxdi': True, 'uid' : uid, 'filename': os.path.basename(datafile)})
                bold_msg('wrote %s' % datafile)
                if not is_re_worker_active():
                    BMM_log_info(f'energy scan finished, uid = {uid}, scan_id = {bmm_catalog[uid].metadata["start"]["scan_id"]}\ndata file written to {datafile}')
                else:
                    BMM_log_info(f'energy scan finished, uid = {uid}\ndata file written to {datafile}')
                    
                ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
                ## data evaluation + message to Slack
                ## also sync data with Google Drive
                if any(md in p['mode'] for md in ('trans', 'fluo', 'flou', 'both', 'ref', 'xs', 'xs1', 'xs4', 'xs7', 'yield')):
                    try:
                        score, emoji = user_ns['clf'].evaluate(uid, mode=plotting_mode(p['mode']))
                        report(f"ML data evaluation model: {emoji}", level='bold', slack=True)
                        if score == 0:
                            report(f'An {emoji} may not mean that there is anything wrong with your data. See https://tinyurl.com/2xp2bhbx', level='whisper', slack=True)
                            with open(os.path.join(WORKSPACE, 'logs', 'failed_data_evaluation.txt'), 'a') as f:
                                f.write(f'{now()}\n\tmode = {p["mode"]}/{plotting_mode(p["mode"])}\n\t{uid}\n\n')
                    except:
                        pass
                        



            ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
            ## finish up, close out
            
            print('Returning to fixed exit mode') #  and returning DCM to %1.f' % eave)
            dcm.mode = 'fixed'
            #yield from mv(dcm_bragg.acceleration, BMMuser.acc_slow)
            #dcm_bragg.clear_encoder_loss()
            #yield from mv(dcm.energy, eave)
            #yield from mv(dcm_bragg.acceleration, BMMuser.acc_fast)

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## execute this scan sequence plan
        yield from scan_sequence(clargs) #, noreturn)

    def cleanup_plan(inifile):
        print('Finishing up after an XAFS scan sequence')
        BMM_clear_suspenders()

        if not is_re_worker_active():
            how = 'finished  :tada:'
            try:
                if 'primary' not in bmm_catalog[-1].metadata['stop']['num_events']:
                    how = '*stopped*  :warning:'
                elif bmm_catalog[-1].metadata['stop']['num_events']['primary'] != bmm_catalog[-1].metadata['start']['num_points']:
                    how = '*stopped*  :warning:'
            except:
                how = '*stopped*  :warning:'
        else:
            how = 'finished or stopped on QS'
        if BMMuser.final_log_entry is True:
            report(f'== XAFS scan sequence {how}', level='bold', slack=True)
            if not is_re_worker_active():
                BMM_log_info(f'most recent uid = {bmm_catalog[-1].metadata["start"]["uid"]}, scan_id = {bmm_catalog[-1].metadata["start"]["scan_id"]}')
            else:
                pass

            kafka_message({'dossier' : 'set', 'uidlist' : uidlist, })
            time.sleep(3.0)
        kafka_message({'dossier' : 'write', })

        if len(uidlist) > 0:
            basename = rkvs.get('BMM:dossier:basename').decode('utf-8')
            if basename is None:
                kafka_message({'xafsscan': 'stop', 'filename': None})
            else:
                #kafka_message({'xafsscan': 'stop', 'filename': os.path.join(BMMuser.folder, 'snapshots', f'{basename}_liveplot.png')})
                #kafka_message({'xafs_sequence':'stop', 'filename': os.path.join(BMMuser.folder, 'snapshots', f'{basename}.png')})
                kafka_message({'xafsscan': 'stop', 'filename': f'snapshots/{basename}_liveplot.png', 'uid': uidlist[0]})
                kafka_message({'xafs_sequence':'stop', 'filename': f'snapshots/{basename}.png'})
                
        dcm.mode = 'fixed'
        yield from resting_state_plan()
        yield from sleep(1.0)
        yield from mv(dcm_pitch.kill_cmd, 1)
        yield from mv(dcm_roll.kill_cmd, 1)

    RE, BMMuser, dcm, dwell_time = user_ns['RE'], user_ns['BMMuser'], user_ns['dcm'], user_ns['dwell_time']
    dcm_bragg, dcm_pitch, dcm_roll, dcm_x = user_ns['dcm_bragg'], user_ns['dcm_pitch'], user_ns['dcm_roll'], user_ns['dcm_x']
    xafs_wheel, ga, linkam, gmb, lakeshore = user_ns['xafs_wheel'], user_ns['ga'], user_ns['linkam'], user_ns['gmb'], user_ns['lakeshore']
    refl = user_ns['refl']
    rkvs = user_ns['rkvs']

    try:
        dualio = user_ns['dualio']
    except:
        pass
    
    ######################################################################
    # this is a tool for verifying a macro.  this replaces an xafs scan  #
    # with a sleep, allowing the user to easily map out motor motions in #
    # a macro                                                            #
    if BMMuser.macro_dryrun:
        inifile, estimate = howlong(inifile, interactive=False, **kwargs)
        (p, f) = scan_metadata(inifile=inifile, **kwargs)
        if 'filename' in p:
            info_msg('\nBMMuser.macro_dryrun is True.  Sleeping for %.1f seconds at sample "%s".\n' %
                     (BMMuser.macro_sleep, p['filename']))
        else:
            info_msg('\nBMMuser.macro_dryrun is True.  Sleeping for %.1f seconds.\nAlso there seems to be a problem with "%s".\n' %
                     (BMMuser.macro_sleep, inifile))
        countdown(BMMuser.macro_sleep)
        return(yield from null())
    ######################################################################
    dossier = DossierTools()
    uidlist = []
    basename = None
    BMMuser.final_log_entry = True
    RE.msg_hook = None
    if BMMuser.lims is False:
        BMMuser.snapshots = False
        BMMuser.htmlout   = False
    else:
        BMMuser.snapshots = True
        BMMuser.htmlout   = True
        
    if is_re_worker_active():
        inifile = DEFAULT_INI
    if inifile is None:
        inifile = present_options('ini')
    if inifile is None:
        return(yield from null())
    if inifile[-4:] != '.ini':
        inifile = inifile+'.ini'
    yield from finalize_wrapper(main_plan(inifile, **kwargs), cleanup_plan(inifile))
    RE.msg_hook = BMM_msg_hook


def remake_dossier(stub=None, uidlist=None, folder=None):
    '''Regenerate a dossier given a list of UIDs for the xafs scan
    repetitions in the measurement.

    arguments
    =========
    stub: (str)
      The filename stub of the group of files.  If not provided, it
      will be gleaned from the first record in uidlist

    uidlist: (list of strings)
      A list of UID strings for the database records of the xafs scans
      to be captured in the dossier page.  All UIDs will be checked to
      verify that they are for xafs scans

    folder: (str)
      The fully resolved path to the proposal folder to which the dossier 
      file should be written.  If not provided, the current value of 
      BMMuser.folder will be used.


    A good command line trick for getting the UIDs:

       grep uid myfile.00[1-7] | cut -d ' ' -f 3

    This snarfs the UIDs from the headers of a group of xafs XDI
    files.

    '''
    if folder is None:          # use current proposal folder if None
        folder = proposal_base()
    if os.path.exists(folder) is False:
        print(f'The folder provided does not exist or is inaccessible: {folder}.')
        return        
    if uidlist is None:         # require a list of UID strings
        print('No uidlist provided.')
        return
    if all('xafs' in bmm_catalog[x].metadata['start']['plan_name'] for x in uidlist) is False:  # all UIDs must point to an xafs record
        print('The uidlist contains records that are not xafs scans.')
        return
    if stub is None:            # snarf stub from first UID if not provided
        first = bmm_catalog[uidlist[0]].metadata['start']['XDI']['_filename']
        stub = os.path.splitext(first)[0]
        
    kafka_message({'dossier': 'start', 'stub': stub})
    kafka_message({'dossier': 'set', 'folder': folder, 'rid': str(uuid.uuid4())[:8]})
    kafka_message({'dossier': 'set', 'uidlist': uidlist})
    kafka_message({'dossier': 'write'})


    
def xanes(filename=None, step=2):
    '''Measure one repetition of a quick-n-dirty XANES scan from -30 to
    +40 using the element and edge currently reported by redis.

    attributes
    ==========
    filename: str
       Filename stub, default is {el}-{ed}-testXANES

    step: float
       step size in eV, default is 2 eV

    '''
    rkvs = user_ns['rkvs']
    params = {'bounds' : '-30 -10 20 40', 'steps' : f'5 {step} {step*2}', 'times': '0.5 0.5 0.5'}
    el = rkvs.get("BMM:pds:element").decode("utf-8")
    ed = rkvs.get("BMM:pds:edge").decode("utf-8")
    if filename is None:
        filename = f'{el}-{ed}-testXANES'
    comment = 'quick-n-dirty XANES scan'
    yield from xafs(DEFAULT_INI, filename=filename, element=el, sample=comment, prep=comment, comment=comment,
                    mode='fluorescence', edge=ed, experimenters=user_ns['BMMuser'].experimenters, snapshots=False, copy=False, **params)
    

def howlong(inifile=None, interactive=True, **kwargs):
    '''
    Estimate how long the scan sequence in an XAFS control file will take.
    Parameters from control file are composable via kwargs.

    Examples
    --------
    Interactive (command line) use:
        
    >>> howlong('scan.ini')

    Non-interactive use (for instance, to display the control file contents and a time estimate):
        
    >>> howlong('scan.ini', interactive=False)

    '''

    ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
    ## user input, find and parse the INI file
    ## try inifile as given then workspace + inifile
    ## this allows something like RE(xafs('myscan.ini')) -- short 'n' sweet
    if is_re_worker_active():
        inifile = '/nsls2/data3/bmm/shared/config/xafs/scan.ini'
    BMMuser = user_ns['BMMuser']
    if inifile is None:
        inifile = present_options('ini')
    if inifile is None:
        return('', -1)
    if inifile[-4:] != '.ini':
        inifile = inifile+'.ini'
    orig = inifile
    if not os.path.isfile(inifile):
        inifile = os.path.join(BMMuser.workspace, inifile)
        if not os.path.isfile(inifile):
            warning_msg('\n%s does not exist!  Bailing out....\n' % orig)
            return(orig, -1)
    bold_msg('reading ini file: %s' % inifile)
    (p, f) = scan_metadata(inifile=inifile, **kwargs)
    if not p:
        error_msg('%s could not be read as an XAFS control file\n' % inifile)
        return(orig, -1)
    (ok, missing) = ini_sanity(f)
    if not ok:
        error_msg('\nThe following keywords are missing from your INI file: '), '%s\n' % str.join(', ', missing)
        return(orig, -1)
    (energy_grid, time_grid, approx_time, delta) = conventional_grid(p['bounds'], p['steps'], p['times'], e0=p['e0'], element=p['element'], edge=p['edge'], ththth=p['ththth'])
    #if delta == 0:
    text = f'One scan of {len(energy_grid)} points will take about {approx_time:.1f} minutes\n'
    text +=f'The sequence of {inflect("scan", p["nscans"])} will take about {approx_time * int(p["nscans"])/60:.1f} hours'
    #else:
    #    text = f'One scan of {len(energy_grid)} points will take {approx_time:.1f} minutes +/- {delta:.1f} minutes \n'
    #    text +=f'The sequence of {inflect("scan", p["nscans"])} will take about {approx_time * int(p["nscans"])/60:.1f} hours +/- {delta*numpy.sqrt(int(p["nscans"])):.1f} minutes'


    if interactive:
        bt = '\n'
        for k in ('bounds', 'bounds_given', 'steps', 'times'):
            addition = '      %-13s : %-50s\n' % (k,p[k])
            bt = bt + addition.rstrip() + '\n'
        for (k,v) in p.items():
            if k in ('bounds', 'bounds_given', 'steps', 'times'):
                continue
            if k in ('npoints', 'dwell', 'delay', 'inttime', 'channelcut', 'bothways'):
                continue
            addition = '      %-13s : %-50s\n' % (k,v)
            bt = bt + addition.rstrip() + '\n'
        for k in ('post_webcam', 'post_anacam', 'post_usbcam1', 'post_usbcam2', 'post_xrf'):
            addition = '      %-13s : %-50s\n' % (k,getattr(user_ns['BMMuser'], k))
            bt = bt + addition.rstrip() + '\n'
            
        boxedtext(bt, title='Control file contents', color='cyan')
        print(text)
    else:
        return(inifile, text)


def xafs_grid(inifile=None, **kwargs):
    '''
    Return the energy and time grids specified in an INI file.

    '''

    ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
    ## user input, find and parse the INI file
    ## try inifile as given then workspace + inifile
    ## this allows something like RE(xafs('myscan.ini')) -- short 'n' sweet
    BMMuser = user_ns['BMMuser']
    if inifile is None:
        inifile = present_options('ini')
    if inifile is None:
        return('', -1)
    if inifile[-4:] != '.ini':
        inifile = inifile+'.ini'
    orig = inifile
    if not os.path.isfile(inifile):
        inifile = os.path.join(BMMuser.workspace, inifile)
        if not os.path.isfile(inifile):
            warning_msg('\n%s does not exist!  Bailing out....\n' % orig)
            return(orig, -1)
    bold_msg('reading ini file: %s' % inifile)
    (p, f) = scan_metadata(inifile=inifile, **kwargs)
    if not p:
        error_msg('%s could not be read as an XAFS control file\n' % inifile)
        return(orig, -1)
    (ok, missing) = ini_sanity(f)
    if not ok:
        error_msg('\nThe following keywords are missing from your INI file: '), '%s\n' % str.join(', ', missing)
        return(orig, -1)
    (energy_grid, time_grid, approx_time, delta) = conventional_grid(p['bounds'], p['steps'], p['times'], e0=p['e0'], element=p['element'], edge=p['edge'], ththth=p['ththth'])
    print(f'{p["element"]} {p["edge"]}')
    return(energy_grid, time_grid)
