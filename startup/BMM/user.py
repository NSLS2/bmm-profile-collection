import sys, os, re, shutil, socket, datetime, time, requests
from distutils.dir_util import copy_tree
import json, pprint, copy, textwrap
from subprocess import run
import warnings

from rich import print as cprint

from nslsii.utils import open_redis_client
from nslsii.sync_experiment import sync_experiment, validate_proposal
#print(start_experiment.__globals__['__file__'])

try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False


from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)
facility_dict = md = user_ns["RE"].md

from bmm_tools.tools.messages import *  # error_msg et al. + boxedtext
from bmm_tools.tools.periodictable import edge_energy
import bmm_tools.tools.md
from bmm_tools.tools.md import proposal_base

from BMM.functions    import BMM_STAFF, LUSTRE_XAS, LUSTRE_DATA_ROOT
from BMM.workspace    import rkvs
from BMM.user_ns.bmm  import kafka
from BMM.logging      import BMM_user_log, BMM_unset_user_log, report

from BMM.user_ns.base import startup_dir, profile_configuration, PROPOSALS

TEMPLATES_FOLDER = 'templates'



try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False

#run_report(__file__, text='user definitions and start/stop an experiment')

## sort of a singleton, see http://code.activestate.com/recipes/66531/
class Borg:
    __shared_state = {}
    def __init__(self):
        self.__dict__ = self.__shared_state      

class BMM_User(Borg):
    '''A class for managing the user interaction at BMM.

    Experiment attributes
    ---------------------
    DATA : str
        path to folder containing data
    prompt : bool
        True prompt at beginning of plans
    final_log_entry : bool
        True write log entries during plan cleanup
    date : str
        start date of experiment as YYYY-MM-DD
    gup : str
        GUP number
    saf : str
        SAF number
    cycle : str
        NSLS-II ops cycle (e.g. '2020-1')
    name : str
        full name of PI
    staff : bool
        True if a staff experiment
    macro_dryrun : bool
        True will replace a call to xafs() with a sleep
    macro_sleep : float
        the length of that sleep
    motor_fault : str or None
        normally None, set to a string when motors are found in a fault state
    detector : int
        4=4-element detector, 1=1-element detector
    echem : bool
        deprecated
    echem_remote : str
        deprecated
    instrument : str
        name of sample instrument, e.g. "sample wheel" or "glancing angle stage"
    syns : bool 
        True is any MCS8 axes are disconnected and defined as SynAxis
    display_img : None or PIL object
        Carries the most recently displayed PIL object

    Current plot attributes
    -----------------------
    motor : str
        fast motor in current plot
    motor2 : str
        slow motor in current plot
    fig : matplotlib.figure.Figure
        figure object of current plot
    ax : matplotlib.axes._subplots.AxisSubplot
        axis object of current plot
    x : int
        plucked-upon X coordinate
    y : int
        plucked-upon Y coordinate


    Energy scan control attributes, default values
    ----------------------------------------------
    pds_mode : str
        photon delivery system mode (A, B, C, D, E, F, XRD)
    bounds : list of float or str
        list of energy or k scan boundaries
    steps : list of float or str
        list of energy ot k steps
    times : list of float or str
        list of integration times
    folder : str
        data folder on Lustre
    folder_link : str
        local link to Lustre data folder
    filename : str
        output data file stub
    experimenters : str
        names of experimenters
    element : str
        absorbing element
    edge : str
        absorption edge
    sample : str
        sample composition or stoichiometry
    prep : str
        how sample was prepared for measurement
    comment : str
        anything else of interest about the sample
    nscans : int
        number of scan repititions
    start : int
        starting scan number
    lims : bool
        perform LIMS chores (snapshots, XRF spectra, dossier)
    snapshots : bool
        take snapshots
    usbstick : bool
        rewrite USB-stick-safe filenames
    rockingcurve : bool
        do a rocking curve scan at the pseudo-channel-cut energy
    htmlpage : bool
        write dossier
    bothways : bool
        measuring in both directions on mono
    channelcut : bool
        measuring in pseudo-channel-cut mode
    ththth : bool
        measuring with the Si(333) reflection
    mode : str
        in-scan plotting mode

    Single energy time scan attributes, default values
    --------------------------------------------------
    npoints : int
        number of time points
    dwell : float
        dwell time at each time step
    delay : float
        delay between time steps
    shutter : bool 
        True to close and open shutter to minimize beam exposure

    Methods for public use
    ----------------------
    begin_experiment(self, name=None, date=None, gup=0, saf=0)

    end_experiment(self, force=False)

    show_experiment(self)
    '''
    def __init__(self):
        ## experiment attributes
        self.DATA            = os.path.join(os.getenv('HOME'), 'Data', 'bucket') + '/'
        self.prompt          = True
        self.final_log_entry = True
        self.date            = ''
        self.gup             = 0
        self.saf             = 0
        # e.g. self.cycle = '2022-2'
        cnum = (0,1,1,1,1,2,2,2,2,3,3,3,3)[int(datetime.datetime.now().strftime("%m"))]
        self.cycle           = f'{datetime.datetime.now().strftime("%Y")}-{cnum}'
        self.host            = socket.gethostname()
        self.name            = None
        self.staff           = False
        #self.read_foils      = None
        #self.read_rois       = None
        self.user_is_defined = False
        self.motor_fault     = None
        self.detector        = 4
        self.echem           = False  # deprecated
        self.echem_remote    = None   # deprecated
        self.use_slack       = True
        self.slack_channel   = None
        self.trigger         = False
        self.instrument      = ''
        self.running_macro   = False
        self.suspenders_engaged = False
        self.display_img     = None
        
        self.macro_dryrun    = False  ############################################################################
        self.macro_sleep     = 2      # These are used to help macro writers test motor motions in their macros. #
                                      # When true, this will turn xafs scans, line scans, change_edge, etc into  #
                                      # a sleep.  This allows visual inspection of motor movement between scans. #
                                      ############################################################################

        self.readout_mode    = 'struck' ## 'analog'  'xspress3'  'digital'
        self.rois            = list(),

        self.xs1             = None
        self.xs2             = None
        self.xs3             = None
        self.xs4             = None
        self.xs5             = None
        self.xs6             = None
        self.xs7             = None
        self.xs8             = None
        self.xschannel1      = None
        self.xschannel2      = None
        self.xschannel3      = None
        self.xschannel4      = None
        self.xschannel5      = None
        self.xschannel6      = None
        self.xschannel7      = None
        self.xschannel8      = None
                
        ## current plot attributes    #######################################################################
        self.motor    = None          # these are used to keep track of mouse events on the plotting window #
        self.motor2   = None          # see 70-linescans.py, and 71-areascan.py                             #
        self.fig      = None          #######################################################################
        self.ax       = None
        self.x        = None
        self.y        = None
        self.prev_fig = None
        self.prev_ax  = None
        #self.all_figs = []

        ## scan control attributes
        self.pds_mode      = None
        self.bounds        = [-200, -30, 15.3, '14k']  ## scan grid parameters
        self.steps         = [10, 0.5, '0.05k']
        self.times         = [0.5, 0.5, '0.25k']
        self.folder        = os.path.join(os.getenv('HOME'), 'Data', 'bucket')  # DEPRECATED
        self.folder_link   = None  # DEPRECATED
        self.filename      = 'data.dat'
        self.experimenters = ''
        self.element       = None
        self.e0            = None
        self.energy        = None
        self.edge          = 'K'
        self.sample        = ''
        self.prep          = ''
        self.comment       = ''
        self.nscans        = 1
        self.start         = 0
        self.inttime       = 1
        self.snapshots     = True
        self.usbstick      = False
        self.rockingcurve  = False
        self.htmlpage      = True
        self.bothways      = False
        self.channelcut    = True
        self.ththth        = False
        self.lims          = True
        self.mode          = 'transmission'
        self.url           = False
        self.doi           = False
        self.cif           = False
        self.npoints       = 0     ###########################################################################
        self.dwell         = 1.0   ## parameters for single energy absorption detection, see 72-timescans.py #
        self.delay         = 0.1   ###########################################################################
        self.shutter       = False
        
        ## mono acceleration control
        self.acc_fast      = 0.4   ###########################################################################
        self.acc_slow      = 0.5   # after decreasing Bragg acceleration time, Bragg axis would occasionally #
                                   # freeze. these are used to try to mitigate this problem                  #
                                   ###########################################################################
        
        self.bender_xas    = 212225  #####################################################################
        self.bender_xrd    = 107240  # approximate values for M2 bender for focusing at XAS & XRD tables #
        self.bender_margin = 10000   #####################################################################

        self.filter_state  = 0

        self.extra_metadata = None
        self.syns           = False

        self.post_webcam    = False  ########################################################
        self.post_usbcam1   = False  # parameters for controlling what gets posted to Slack #
        self.post_usbcam2   = False  ########################################################
        self.post_cam8      = False
        self.post_cam9      = False
        self.post_anacam    = False
        self.post_xrf       = False

        self.mouse_click    = None
        
        self.tweak_xas_time = 18.0  # this is a fudge factor to get XAS time approximation to work, see xafs_functions.py l.221
        self.enable_live_plots = False
        
        self.bmm_strings  = ("DATA", "date", "host", "name", "instrument",
                             "readout_mode", "workspace", "filename",
                             "experimenters", "element", "edge", "sample", "prep", "comment",
                             "xs1", "xs2", "xs3", "xs4", "xs5", "xs6", "xs7", "xs8", "pds_mode", "mode")
        self.bmm_ints     = ("gup", "saf", "detector", "npoints", "bender_xas", "bender_xrd",
                             "bender_margin", "filter_state", "nscans", "start")
        self.bmm_floats   = ("macro_sleep", "dwell", "delay", "acc_fast", "acc_slow",
                             "inttime", "tweak_xas_time") #, "edge_energy")
        self.bmm_booleans = ("prompt", "final_log_entry", "staff",
                             "use_slack", "trigger", "running_macro", "suspenders_engaged",
                             "macro_dryrun", "snapshots", "usbstick", "rockingcurve",
                             "htmlpage", "bothways", "channelcut", "ththth", "lims", "url",
                             "doi", "cif", "syns", "enable_live_plots",
                             "post_webcam", "post_anacam", "post_usbcam1", "post_usbcam2", "post_xrf", "post_cam8", "post_cam9")
        self.bmm_none     = ("slack_channel", "extra_metadata")
        self.bmm_ignore   = ("motor_fault", "bounds", "steps", "times", "motor", "motor2",
                             "fig", "ax", "x", "y", "prev_fig", "prev_ax", 'display_img')
        self.bmm_obsolete = ("read_rois", "e0", "gdrive", "do_gdrive",
                             "rois", "roi_channel", "echem", "echem_remote", 'use_pilatus', "folder", "folder_link",
                             'roi1', 'roi2', 'roi3', 'roi4',
                             'dtc1', 'dtc2', 'dtc3', 'dtc4',)


    def state_to_redis(self, filename=None, prefix='', verbose=False):

        all_keys = list(self.__dict__.keys())
        almost_all_keys = [n for n in all_keys
                           if n not in ('fig', 'ax', 'prev_fig', 'prev_ax', 'display_img',
                                        'motor', 'cycle', 'user_is_defined') and
                           'xschannel' not in n]
        d = dict()
        for k in self.bmm_strings:
            d[k] = getattr(self, k)
            if verbose: print(f'string: {k} = >{getattr(self, k)}<')
            if getattr(self, k) is None:
                rkvs.set(f'BMM:user:{k}', 'None')
            else:
                rkvs.set(f'BMM:user:{k}', getattr(self, k))
        for k in self.bmm_ints:
            d[k] = getattr(self, k)
            if verbose: print(f'int: {k} = >{getattr(self, k)}<')
            if getattr(self, k) is None:
                rkvs.set(f'BMM:user:{k}', 0)
            else:
                rkvs.set(f'BMM:user:{k}', getattr(self, k))
        for k in self.bmm_floats:
            d[k] = getattr(self, k)
            if verbose: print(f'float: {k} = >{getattr(self, k)}<')
            if getattr(self, k) is None:
                rkvs.set(f'BMM:user:{k}', 0.0)
            else:
                rkvs.set(f'BMM:user:{k}', getattr(self, k))
        for k in self.bmm_booleans:
            d[k] = getattr(self, k)
            if verbose: print(f'bool: {k} = >{getattr(self, k)}<')
            if getattr(self, k) is None:
                rkvs.set(f'BMM:user:{k}', 'False')
            else:
                rkvs.set(f'BMM:user:{k}', str(getattr(self, k)))
        for k in self.bmm_none:
            d[k] = getattr(self, k)
            if verbose: print(f'none: {k} = >{getattr(self, k)}<')
            setattr(self, k, '')
        
        #for k in almost_all_keys:
        #    d[k] = getattr(self, k)
        #    rkvs.set(f'BMM:user:{k}', str(d[k]))
        print(f'{prefix}wrote BMMuser state to redis')
        
        if filename is None:
            print(json.dumps(d, indent=4))
        else:
            with open(filename, 'w') as outfile:
                json.dump(d, outfile, indent=4)
            print(f'{prefix}wrote BMMuser state to {filename}')

    def set_element(self, element, edge=None):
        if edge is None:
            energy = edge_energy(element,'K')
            self.element = element
            if energy > 23500:
                self.edge = 'L3'
            else:
                self.edge = 'K'
        else:
            self.element = element
            self.edge    = edge
        for i in range(1,9):
            setattr(self, f'xs{i}', f'{element}{i}')
        for key in ('element', 'edge', 'xs1', 'xs2', 'xs3', 'xs4', 'xs5', 'xs6', 'xs7', 'xs8'):
            rkvs.set(f'BMM:user:{key}', getattr(self, key))

    def verify_roi(self, xs, el, edge, tab=''):
        print(f'{tab}Setting ROIs on {xs.name} for {el} {edge} edge')
        try:
            ## if el is not one of the "standard" 12 ROI sets, insert it into xs.slots[12]/index 13
            if xs.check_element(el, edge):
                forceit = False
                if el.capitalize() in ('Pb', 'Au', 'Pt') and edge.capitalize() in ('L2', 'L1'):
                    forceit = True # Pb and Pt L3 edges are "standard" ROIs
                if el not in xs.slots or forceit:
                    with open(os.path.join(startup_dir, 'rois.json'), 'r') as fl:
                        js = fl.read()
                    allrois = json.loads(js)
                    xs.slots[-2] = el
                    for channel in xs.iterate_channels():
                        xs.set_roi_channel(channel, index=19, name=f'{el.capitalize()}',
                                           low =allrois[el.capitalize()][edge.lower()]['low'],
                                           high=allrois[el.capitalize()][edge.lower()]['high'])
                    xs.set_rois()

                xs.measure_roi()
            else:
                report(f'{tab}No tabulated ROIs for the {el.capitalize()} {edge.capitalize()} edge.  Not setting ROIs for mesaurement.',
                       level='bold', slack=True)
            xs.reset_rois(tab=tab, quiet=True)
        except Exception as E:
            error_msg(E)

            
    def state_from_redis(self):
        from BMM.workspace import rkvs
        verbose = False
        for k in self.bmm_strings:
            if verbose: print("string:", k)
            try:
                setattr(self, k, rkvs.get(f'BMM:user:{k}').decode('utf-8'))
            except AttributeError:
                setattr(self, k, '')
        for k in self.bmm_ints:
            if verbose: print("int:", k)
            if rkvs.get(f'BMM:user:{k}').decode('utf-8').strip() == '':
                setattr(self, k, 0)
            else:
                setattr(self, k, int(rkvs.get(f'BMM:user:{k}').decode('utf-8')))
        for k in self.bmm_floats:
            if verbose: print("float:", k)
            try:
                setattr(self, k, float(rkvs.get(f'BMM:user:{k}').decode('utf-8')))
            except AttributeError:
                setattr(self, k, 0.0)
        for k in self.bmm_booleans:
            if verbose: print("bool:", k)
            try:
                this = rkvs.get(f'BMM:user:{k}').decode('utf-8')
                if this.lower() in ('false', 'no', '0', 'f', 'n'):
                    setattr(self, k, False)
                else:
                    setattr(self, k, True)
            except AttributeError:
                setattr(self, k, False)
                
        for k in self.bmm_none:
            if verbose: print("none:", k)
            setattr(self, k, None)
        
        rkvs.set('BMM:pds:element',     self.element)
        rkvs.set('BMM:pds:edge',        self.edge)
        rkvs.set('BMM:pds:edge_energy', edge_energy(self.element, self.edge))
            
    def show(self, scan=False):
        '''
        Show the current contents of the BMMuser object
        '''
        print('Experiment attributes:')
        for att in ('DATA', 'prompt', 'final_log_entry', 'date', 'gup', 'saf', 'name', 'staff', 
                    'user_is_defined', 'pds_mode', 'macro_dryrun', 'macro_sleep', 'motor_fault',
                    'detector'):
            print('\t%-15s = %s' % (att, str(getattr(self, att))))

        print('\nROI control attributes:')
        for att in ('xs1', 'xs2', 'xs3', 'xs4',
                    'xschannel1', 'xschannel2', 'xschannel3', 'xschannel4'):
            print('\t%-15s = %s' % (att, str(getattr(self, att))))

        print('\nCurrent plot attributes:')
        for att in ('motor', 'motor2', 'fig', 'ax', 'x', 'y'):
            print('\t%-15s = %s' % (att, str(getattr(self, att))))

        print('\nMono acceleration and M2 bender attributes:')
        for att in ('acc_fast', 'acc_slow', 'bender_xas', 'bender_xrd', 'bender_margin'):
            print('\t%-15s = %s' % (att, str(getattr(self, att))))

        if scan:
            print('\nScan control attributes:')
            for att in ('pds_mode', 'bounds', 'steps', 'times', 'folder', 'workspace', 'filename',
                        'experimenters', 'element', 'edge', 'sample', 'prep', 'comment', 'nscans', 'start', 'inttime',
                        'snapshots', 'usbstick', 'rockingcurve', 'htmlpage', 'bothways', 'channelcut', 'ththth', 'mode', 'npoints',
                        'dwell', 'delay'):
                print('\t%-15s = %s' % (att, str(getattr(self, att))))


    def establish_folder(self, i, text, folder):
        '''Locate or create the folders we need for this user.
        '''
        if not os.path.isdir(folder):
            os.makedirs(folder)
            verb, pad = 'Created', ''
        else:
            verb, pad = 'Found', '  '
        self.print_verb_message(i, verb, text, pad, folder)
        return(verb)

    def kafka_establish_folder(self, i, text, folder):
        base = os.path.join(PROPOSALS, facility_dict['cycle'], facility_dict['data_session'])
        kafka.message({'mkdir': os.path.join(base, folder)}) 
        self.print_verb_message(i, 'Verifed', text, '', folder)
        return('Verified')
        

    def find_or_copy_file(self, i, text, fname):
        src     = os.path.join(startup_dir, fname)
        dst     = os.path.join(PROPOSALS, facility_dict['cycle'], facility_dict['data_session'])
        wsp     = self.workspace
        if 'xlsx' in fname:
            src = os.path.join(startup_dir, 'xlsx', fname)
            dst = os.path.join(dst, "templates")
            wsp = os.path.join(self.workspace, "templates")

        kafka.message({'copy' : True, 'file': src, 'target': dst})
        shutil.copyfile(src, os.path.join(wsp, fname))
        verb, pad = 'Copied', ' '
        self.print_verb_message(i, verb, text, pad, dst)
        
    def print_verb_message(self, i, verb, text, pad, result):
        if i == 0:
            print(f'    {verb} {text:28}{pad}   {result:65}')
        else:
            print(f'{i:2d}. {verb} {text:28}{pad}   {result:65}')


    def new_experiment(self, folder, gup=0, saf=0, name='Betty Cooper'):
        '''
        Do the work of prepping for a new experiment.  This will:
          * Create a data folder and it's subfolders
          * Set up the experimental log, creating an experiment.log file, if needed
          * Write templates for scan.ini and macro.py + xlsx templates, if needed
          * Make folders for XRF, HDF5, Pilatus, and electrochemistry
          * Set the GUP and SAF numbers as metadata

        Parameters
        ----------
        folder : str
            data destination
        gup : str
            GUP number
        saf : str
            SAF number
        name : str
            name of PI
        '''

        step = 1
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## Main folders, point BMMuser and wmb objects at data folder
        data_folder = os.path.join(folder, self.date)
        #user_ns['DATA'] = data_folder + '/'
        try:
            hdf5folder = os.path.join('/nsls2', 'data', 'bmm', 'assets', 'xspress3', *self.date.split('-'))
            user_ns['xs'].hdf5.read_path_template = hdf5folder
            user_ns['xs'].hdf5.write_path_template = hdf5folder
            user_ns['xs'].hdf5.file_path.put(hdf5folder)
        except:
            # if we are starting up bsui, the xs folder will get set
            # later.  the try block is needed when running
            # start_experiment from a running bsui instance.
            pass
        try:
            user_ns['wmb'].folder = data_folder + '/'
        except:
            pass


        base = os.path.join(PROPOSALS, facility_dict['cycle'], facility_dict['data_session'])
        
        imagefolder    = 'snapshots'
        prjfolder      = 'prj'
        htmlfolder     = 'dossier'
        templatefolder = "templates"
        self.kafka_establish_folder(step, 'Lustre folder', folder)
        #self.kafka_establish_folder(0,    'data folder', data_folder)
        self.kafka_establish_folder(0,    'snapshot folder', imagefolder)
        self.establish_folder(0,          'snapshot folder', os.path.join(self.workspace, imagefolder))
        self.kafka_establish_folder(0,    'Athena prj folder', prjfolder)
        self.establish_folder(0,          'Athena prj folder', os.path.join(self.workspace, prjfolder))
        self.kafka_establish_folder(0,    'dossier folder', htmlfolder)
        # 'sample.tmpl', 'sample_xs.tmpl', 'sample_ga.tmpl'

        for f in ('manifest.tmpl', 'logo.png', 'message.png', 'plot.png', 'camera.png', 'blank.png',
                  'style.css', 'trac.css', 'messagelog.css'):
            kafka.message({'copy': True,
                           'file': os.path.join(startup_dir, 'dossier', f),
                           'target': os.path.join(base, 'dossier'), })
        kafka.message({'touch': os.path.join(base, 'dossier', 'MANIFEST')})
        print('    copied html generation files, touched MANIFEST')
        self.kafka_establish_folder(0,    'templates folder', templatefolder)
        self.establish_folder(0,         'templates folder', os.path.join(self.workspace, templatefolder))
     
        step += 1

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## setup logger
        # BMM_user_log(os.path.join(data_folder, 'experiment.log'))
        # print('%2d. Set up experimental log file:          %-75s' % (step, os.path.join(data_folder, 'experiment.log')))
        # step += 1

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## scan.ini template, macro template & wheel/ga spreadsheets
        initmpl = os.path.join(startup_dir, 'tmpl', 'xafs.tmpl')
        scanini = os.path.join(self.workspace, "templates", 'xafs.ini')
        if not os.path.isfile(scanini):
            with open(initmpl) as f:
                content = f.readlines()
            o = open(scanini, 'w')
            o.write(''.join(content).format(folder=data_folder, name=self.experimenters))
            o.close()
            verb, pad = 'Copied', ' '
        else:
            verb, pad = 'Found', '  '
        self.print_verb_message(step, verb, 'XAFS INI file', pad, scanini)
        kafka.message({'copy': True,
                       'file': scanini,
                       'target': os.path.join(base, "templates")})

        initmpl = os.path.join(startup_dir, 'tmpl', 'rasterscan.tmpl')
        scanini = os.path.join(self.workspace, "templates", 'raster.ini')
        if not os.path.isfile(scanini):
            with open(initmpl) as f:
                content = f.readlines()
            o = open(scanini, 'w')
            o.write(''.join(content).format(folder=data_folder, name=self.experimenters))
            o.close()
            verb, pad = 'Copied', ' '
        else:
            verb, pad = 'Found', '  '
        self.print_verb_message(0, verb, 'raster INI file', pad, scanini)
        kafka.message({'copy': True,
                       'file': scanini,
                       'target': os.path.join(base, "templates")})

        initmpl = os.path.join(startup_dir, 'tmpl', 'sead.tmpl')
        scanini = os.path.join(self.workspace, "templates", 'sead.ini')
        if not os.path.isfile(scanini):
            with open(initmpl) as f:
                content = f.readlines()
            o = open(scanini, 'w')
            o.write(''.join(content).format(folder=data_folder, name=self.experimenters))
            o.close()
            verb, pad = 'Copied', ' '
        else:
            verb, pad = 'Found', '  '
        kafka.message({'copy': True,
                       'file': scanini,
                       'target': os.path.join(base, "templates")})
                      
        self.print_verb_message(0, verb, 'sead INI file', pad, scanini)


        macrotmpl = os.path.join(startup_dir, 'tmpl', 'macro.tmpl')
        macropy = os.path.join(self.workspace, 'templates', 'sample_macro.py')
        commands = '''
        ## sample 1
        yield from slot(1)
        yield from xafs('scan.ini', filename='samp1', sample='first sample')
        close_plots()

        ## sample 2
        yield from slot(2)
        ## yield from mvr(xafs_x, 0.5)
        yield from xafs('scan.ini', filename='samp2', sample='another sample', comment='my comment')
        close_plots()

        ## sample 3
        yield from slot(3)
        yield from xafs('scan.ini', filename='samp3', sample='a different sample', prep='this sample prep', nscans=4)
        close_plots()'''

        if not os.path.isfile(macropy):
            with open(macrotmpl) as f:
                content = f.readlines()
            o = open(macropy, 'w')
            o.write(''.join(content).format(folder=self.workspace,
                                            base='sample',
                                            content=commands,
                                            description='(example...)',
                                            instrument='',
                                            cleanup='',
                                            initialize='' ))
            o.close()
            verb, pad = 'Copied', ' '
        else:
            verb, pad = 'Found', '  '
        self.print_verb_message(0, verb, 'macro template', pad, macropy)
        kafka.message({'copy': True,
                       'file': macropy,
                       'target': os.path.join(base, "templates")})

        #self.find_or_copy_file(0, 'wheel macro spreadsheet',           'wheel.xlsx')
        self.find_or_copy_file(0, 'glancing angle spreadsheet',        'glancing_angle.xlsx')
        self.find_or_copy_file(0, 'double wheel spreadsheet',          'wheel.xlsx')
        self.find_or_copy_file(0, 'Linkam stage spreadsheet',          'linkam.xlsx')
        self.find_or_copy_file(0, 'Lakeshore spreadsheet',             'lakeshore.xlsx')
        self.find_or_copy_file(0, 'motor grid spreadsheet',            'grid.xlsx')
        self.find_or_copy_file(0, 'resonant reflectivity spreadsheet', 'reflectivity.xlsx')
        step += 1            
        
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## XRF & HDF5folders
        xrffolder = os.path.join(base, 'XRF')
        self.kafka_establish_folder(step, 'XRF spectra folder', xrffolder)
        #hdf5folder = os.path.join(data_folder, 'raw', 'HDF5')
        #hdf5folder = os.path.join('/nsls2', 'data', 'bmm', 'assets', 'xspress3', *self.date.split('-'))
        #self.establish_folder(0, 'Xspress3 HDF5 folder', hdf5folder)
        #xs.hdf5.file_path.put(hdf5folder)
        
        step += 1

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## SAF and GUP
        self.gup = gup
        self.saf = saf
        print(f'{step:2d}. Set GUP and SAF numbers as metadata    GUP: {gup}   SAF: {saf}')
        shipping_folder = os.path.join(os.getenv('HOME'), 'SDS', self.cycle, f'{saf}')
        self.establish_folder(0, 'shipping docs folder', shipping_folder)
        step += 1

        self.user_is_defined = True
    
        return None

    def begin_experiment(self, name=None, date=None, gup=0, saf=0, startup=False):
        '''
        Get ready for a new experiment.  Run this first thing when a user
        sits down to start their beamtime.  This will:
          * Create a folder, if needed
          * Set up the experimental log, creating an experiment.log file, if needed
          * Write templates for scan.ini and macro.py, if needed
          * Copy some other useful files
          * Make snapshots, dossier, and prj folders
          * Set the GUP and SAF numbers as metadata

        Parameters
        ---------
        name : str
            name of PI
        date : str
            YYYY-MM-DD start date of experiment (e.g. 2018-11-29)
        gup : str
            GUP number
        saf : str
            SAF number
        '''
        do_sync = False
        if self.user_is_defined:
            error_msg('An experiment is already started.')
            return()
        if name is None:
            error_msg('You did not supply the user\'s name')
            return()
        if date is None:
            error_msg('You did not supply the start date')
            return()
        pattern=re.compile(r'\d{4}\-\d{2}\-\d{2}')
        if pattern.fullmatch(date) is None:
            whisper(f'The start date {date} was not in the form YYYY-MM-DD')
            #return()
        if saf == 0:
            error_msg('You did not supply the SAF number')
            return()
        if gup == 0:
            (gup, cyc) = self.fetch_proposal_from_saf(saf)
            if gup == 0:
                error_msg('Error retrieving data from PASS API')
                return()
            if gup == -1:
                error_msg('No proposal found corresponding to SAF')
                return()
        

        ## NSLS-II start experiment infrastructure
        ## this prefix needs to be the same (but without the dash) as the call to RedisJSONDict in user_ns/base.py
        bmm_tools.tools.md.common_re = user_ns['RE']
        bmm_tools.tools.md.common_md = user_ns['RE'].md
        facility_dict = user_ns['RE'].md
        if not is_re_worker_active():  # want to not do this when starting QS environment
            cprint(f'\n[o u chartreuse3]The following authentication is used to write [i]your[/i] data to a folder that [i]you[/i] can access.[/o u chartreuse3]\n')
            cprint(f'[indian_red1]Calling sync_experiment for proposal {gup}. Enter [r]your[/r] BNL username & password at the prompts.[/indian_red1]\n')
            cprint('[indian_red1][u]Anyone[/u] on the current proposal can sign in at this prompt.[/indian_red1]\n')
            warnings.filterwarnings(action='ignore', category=UserWarning, message=r'Experiment pass-\d+ was already started')
            new_md = sync_experiment(gup,
                                     'bmm',
                                     verbose=False,
                                     redis_db=profile_configuration.get('services', 'xas_redis'),
                                     redis_ssl=profile_configuration.get('services', 'redis_ssl'))
            bmm_tools.tools.md.common_md = new_md
            facility_dict = new_md

        #if md['data_session'] in ('pass-301027', 'pass-317886'):  # PU proposal numbers of history
        #    self.experimenters = 'Bruce Ravel'
        #else:
        self.experimenters = ", ".join(list((f"{x['first_name']} {x['last_name']}" for x in validate_proposal(f'pass-{gup}', 'bmm')['users'])))
        
        lustre_root = os.path.join(LUSTRE_DATA_ROOT, f'{self.cycle}', f'pass-{gup}')
        # if not os.path.isdir(lustre_root):
        #     os.makedirs(lustre_root)
        #     do_sync = True
        if name in BMM_STAFF:
            self.staff = True
            local_folder = os.path.join(os.getenv('HOME'), 'Data', 'Staff', name, date)
            #if '' in name:
            #    self.lims = False
        else:
            self.staff = False
            local_folder = os.path.join(os.getenv('HOME'), 'Data', 'Visitors', name, date)
        self.name = name
        self.date = date

        if name in BMM_STAFF:
            user_folder = os.path.join(os.getenv('HOME'), 'Data', 'Staff', name)
            user_workspace = os.path.join(profile_configuration.get('services', 'workspace'), 'Staff', name, date)
        else:
            user_folder = os.path.join(os.getenv('HOME'), 'Data', 'Visitors', name)
            user_workspace = os.path.join(profile_configuration.get('services', 'workspace'), 'Visitors', name, date)
        #if not os.path.isdir(user_folder):
        #    os.makedirs(user_folder)
        if not os.path.isdir(user_workspace):
            os.makedirs(user_workspace)
        if not os.path.isdir(os.path.join(user_workspace, 'templates')):
            os.makedirs(os.path.join(user_workspace, 'templates'))
        self.workspace = user_workspace

        if profile_configuration.getboolean('services', 'proposal_folders_available'):
            self.new_experiment(lustre_root, saf=saf, gup=gup, name=name)
        else:
            cprint('[red1]\t\tProposal folders unavailable[/red1]')
            cprint('[red1]\t\tSkipping BMMuser.new_experiment()[/red1]')
            

        try:
            # direct Slack messages to new proposal channel
            self.bmmbot.refresh_channel()
            kafka.message({'refresh_slack': True})  # do the same in the Kafka workers
        except:
            # at startup, this attribute won't yet exist, but will get
            # set correctly later in the startup process
            pass

        if profile_configuration.getboolean('services', 'proposal_folders_available'):
            if kafka.file_exists(folder=proposal_base(), filename='.introduction_made', number=False) is False:
                #self.welcome_experimenters()
                kafka.message({'touch': os.path.join(proposal_base(), '.introduction_made')})
        else:
            cprint('[red1]\t\tSkipping kafka welcome[/red1]')

        # preserve BMMuser state to a json string #
        self.prev_fig = None
        self.prev_ax  = None
        self.fix()
        self.state_to_redis(filename=os.path.join(self.workspace, '.BMMuser'), prefix=' >> ')
        print('\n\t\t\tNew BMM experiment started')

        jsonfile = os.path.join(os.environ['HOME'], 'Data', '.user.json')
        if os.path.isfile(jsonfile):
            os.chmod(jsonfile, 0o644)
        with open(jsonfile, 'w') as outfile:
            json.dump({'name': name, 'date': date, 'gup' : gup, 'saf' : saf}, outfile)
        os.chmod(jsonfile, 0o444)

        ## update things that use a local folder
        # try:
        #     #xascam._root = os.path.join(self.folder, 'snapshots')
        #     #xrdcam._root = os.path.join(self.folder, 'snapshots')
        #     anacam._root = os.path.join(self.folder, 'snapshots')
        #     #usb1.tiff1.file_path.put(self.folder, 'snapshots')
        #     #usb2.tiff1.file_path.put(self.folder, 'snapshots')
        # except:
        #     pass

        ## update detectors, etc. that rely upon bmm_tools.tools.md to know the current value of RE.md
        bmm_tools.tools.md.common_md = user_ns['RE'].md
        
    def welcome_experimenters(self):
        text = f'''Welcome to the Slack channel for your beamtime at BMM!
 
:speech_balloon: Use this channel for chat.
:atom_symbol: Beamline messages will be posted on #pass-{self.gup}-bmm.

BMM data access: https://nsls2.github.io/bmm-beamline-manual/data.html
Your data folder: `/nsls2/data/bmm/proposals/{user_ns["RE"].md["cycle"]}/pass-{self.gup}`'''
        self.bmmbot.chat_and_pin(text)
        self.bmmbot.client.conversations_invite(channel=self.bmmbot.non_chat_channel, users='U03HA6MM7HT')
        ## user: nsls2_machine_monitor    userid: U03HA6MM7HT
        
        
    def start_experiment_from_serialization(self):
        '''In the situation where bsui needs to be stopped (or crashes) before
        an experiment is properly ended using the end_experiment()
        command, this function will read a json serialization of the
        arguments to the begin_experiment() command.

        If that serialization file is found at bsui start-up, this function
        is run. Thus, the session is immediately ready for the current user.

        In the situation where this start-up script is "%run -i"-ed,
        the fact that self.user_is_defined is True will be recognized.
        '''
        if self.user_is_defined:
            return()

        jsonfile = os.path.join(os.environ['HOME'], 'Data', '.BMMuser')
        self.state_from_redis()
        #if os.path.isfile(jsonfile):
        #    self.state_from_redis(jsonfile)
        self.suspenders_engaged = False
        self.trigger = True
        if self.name is not None:
            self.begin_experiment(name=self.name, date=self.date, gup=self.gup, saf=self.saf, startup=True)

    def show_experiment(self):
        '''Show basic experiment configuration'''
        experimenters = textwrap.wrap(self.experimenters, subsequent_indent='                ')
        print('PI            = %s' % self.name)
        print('Experimenters = %s' % '\n'.join(experimenters))
        print('Date          = %s' % self.date)
        if profile_configuration.getboolean('services', 'proposal_folders_available'):
            print('Data folder   = %s' % proposal_base())
        else:
            cprint('Data folder   = %s' % '[grey58]proposal folders are unavailable[/grey58]')
        print('Work space    = %s' % self.workspace)
        print('GUP           = %s' % self.gup)
        print('SAF           = %s' % self.saf)
        #print('foils = %s' % ' '.join(map(str, user_ns['foils'].slots)))
        #if user_ns['with_xspress3'] is False:
        #    print('ROIs   = %s' % ' '.join(map(str, user_ns['rois'].slots)))

    def end_experiment(self, force=False):
        '''
        Terminate and experiment.

        Unset the logger at the end of an experiment.
        '''

        #self.bmmbot.client.conversations_kick(channel=self.bmmbot.non_chat_channel, users='U03HA6MM7HT')
        ## user: nsls2_machine_monitor    userid: U03HA6MM7HT

        
        if not force:
            if not self.user_is_defined:
                error_msg('There is not a current experiment!')
                return(None)

            
        #####################################################################
        # remove the json serialization of the begin_experiment() arguments #
        #####################################################################

        if os.path.isfile(os.path.join(os.environ['HOME'], 'Data', '.BMMuser')):
            os.remove(os.path.join(os.environ['HOME'], 'Data', '.BMMuser'))
        jsonfile = os.path.join(os.environ['HOME'], 'Data', '.user.json')
        if os.path.isfile(jsonfile):    
            os.chmod(jsonfile, 0o644)
            os.remove(jsonfile)

            
        ###############################################################
        # unset self attributes and experiment specific logger #
        ###############################################################
        BMM_unset_user_log()
        self.folder = os.path.join(os.environ['HOME'], 'Data', 'bucket') + '/'
        user_ns["wmb"].folder = self.folder
        self.date = ''
        self.gup = 0
        self.saf = 0
        self.name = None
        self.staff = False
        self.user_is_defined = False
        for thing in ('name', 'gup', 'saf', 'folder', 'folder_link', 'date'):
            rkvs.set(f'BMM:user:{thing}', '')

        return None

    def set_instrument(self):
        instruments = ('Single wheel', 'Double wheel', 'Linkam stage', 'Displex + LakeShore', 'Glancing angle stage', 'Motor grid')
        print('Select an instrument:\n')
        for i, inst in enumerate(instruments):
            print(f'  {i+1}: {inst}')
        
        print('  u: unset')
        print('\n  r: return')
        choice = input("\nSelect a file > ")
        try:
            if str(choice.lower()) == 'u':
                print('Unsetting instrument')
                self.instrument = ''
                rkvs.set('BMM:user:instrument', '')
                rkvs.set('BMM:automation:type', '')
            elif int(choice) > 0 and int(choice) <= len(instruments):
                this = instruments[int(choice)-1]
                print(f'You selected "{this}"')
                self.instrument = this
                rkvs.set('BMM:user:instrument', this)
                rkvs.set('BMM:automation:type', this)
            else:
                print('No instrument selected')
                return None
        except Exception as e:
            print(e)
            print('No instrument selected')
            return None
        
    def fix(self):
        '''Using the wrong version of a spreadsheet can set booleans
        incorrectly.  This restores them to their defaults.
        '''
        self.snapshots = True
        self.htmlpage = True
        self.usbstick = False
        self.bothways = False
        self.channelcut = True
        self.ththth = False
        self.lims = True

    def cycles(self):
        all_cycles = []
        end = datetime.datetime.now().year + 1
        for y in range(2014, 2028):
            for c in range(1,4):
                all_cycles.append(f'{y}-{c}')
        return all_cycles

    def fetch_proposal_from_saf(self, this=None, cycle=None):
        '''Use the PASS API to determine what proposal number an SAF is
        written against, returning the proposal number.  Works for
        GUP, BDT, PU-P, and PUP.

        If cycle is None, search in the current cycle for the SAF.

        If cycle is specified, if must be a "year-cycle" string like
        '2022-1'

        If cycle is 'all', then all years since the start of NSLS-II
        will be searched
        

        If found, a tuple of GUP number and cycle string is returned.

        If the API server cannot be contacted, the returned GUP number
        is 0 and the cycle string is empty.

        If the SAF number cannot be found, the returned GUP number is
        -1 and the cycle string is empty.

        '''
        cycle_list = [cycle,]
        if cycle is None:
            cycle_list = [user_ns['RE'].md["cycle"],]
        elif type(cycle) is str and cycle.lower() == 'all':
            cycle_list = self.cycles()

        for c in cycle_list:
            url = f'https://api.nsls2.bnl.gov/v1/proposals/?beamline=BMM&facility=nsls2&page_size=100&cycle={c}'
            r = requests.get(url)
            if r.status_code != requests.codes.ok:
                whisper(url)
                return(0, '')           # problem contacting API
            j = json.loads(r.text)
            for prop in j['proposals']:
                for saf in prop['safs']:
                    if str(this) == saf['saf_id']:
                        return(prop['proposal_id'], c)
            return(-1, '')              # didn't find it
