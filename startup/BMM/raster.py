
try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False

import numpy, os, re, shutil, openpyxl, uuid
import textwrap, configparser, datetime
import matplotlib.pyplot as plt

from bluesky.plans import count
from bluesky.plan_stubs import sleep, mv, null
from bluesky.preprocessors import finalize_wrapper

from PIL import Image
from tiled.client import from_profile

from BMMCommon.tools.misc   import now
from BMMCommon.tools.messages import *  # error_msg et al. + boxedtext
from BMMCommon.tools.animated_prompt import PROMPTNC, animated_prompt
from BMMCommon.tools.md import proposal_base

from BMM.areascan        import areascan
from BMM.dossier         import DossierTools
from BMM.functions       import present_options, plotting_mode
from BMM.user_ns.bmm     import kafka
from BMM.logging         import BMM_log_info, BMM_msg_hook, report
from BMM.metadata        import bmm_metadata, display_XDI_metadata, metadata_at_this_moment
from BMM.motor_status    import motor_status
from BMM.resting_state   import resting_state_plan
#from BMM.suspenders      import BMM_suspenders, BMM_clear_to_start, BMM_clear_suspenders

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from BMM.user_ns.base       import bmm_catalog
from BMM.user_ns.dwelltime  import _locked_dwell_time
from BMM.user_ns.suspenders import suspenders

def read_ini(inifile, **kwargs):

    config = configparser.ConfigParser(interpolation=None)
    config.read_file(open(inifile))
    parameters = dict()
    found = dict()

    ## folder cif url doi + various flags
    BMMuser = user_ns['BMMuser']
    parameters['folder'] = BMMuser.folder
    parameters['url'], parameters['doi'], parameters['cif'] = '', '', ''
    
    ## strings
    for a in ('detector', 'filename', 'edge', 'element', 'sample', 'prep', 'comment'):  # 'experimenters'
        found[a] = False
        if a in kwargs:
            parameters[a] = str(kwargs[a])
            found[a] = True
        else:
            try:
                parameters[a] = config.get('scan', a)
                found[a] = True
            except:
                if a in ('edge', 'element'):  # 'experimenters'
                    parameters[a] = getattr(BMMuser, a)
                elif a == 'detector':
                    parameters[a] = 'If'
                else:
                    parameters[a] = None
    parameters['mode'] = 'transmission'
    if parameters['detector'].lower in ('if', 'xs', 'xs1'):
        parameters['mode'] = 'fluorescence'

                    
    ## floats
    for a in ('dwelltime', 'energy'):
        found[a] = False
        if a in kwargs:
            parameters[a] = float(kwargs[a])
            found[a] = True
        else:
            try:
                parameters[a] = float(config.get('scan', a))
                found[a] = True
            except:
                if a == 'dwelltime':
                    parameters[a] = 0.1
                else:
                    parameters[a] = None

    ## booleans
    for a in ('snapshots', 'lims', 'htmlpage', 'usbstick', 'contour', 'log'):
        found[a] = False
        if a not in kwargs:
            try:
                parameters[a] = config.getboolean('scan', a)
                found[a] = True
            except configparser.NoOptionError:
                parameters[a] = True
        else:
            parameters[a] = bool(kwargs[a])
            found[a] = True
                
    parameters['ththth'] = False

                    
    ## fast and slow
    found['fast_motor'], found['fast_start'], found['fast_stop'], found['fast_steps'] = False, False, False, False
    found['slow_motor'], found['slow_start'], found['slow_stop'], found['slow_steps'] = False, False, False, False
    if 'fast' in kwargs:
        this = kwargs['fast'].split()
        parameters['fast_motor'] = this[0]
        parameters['fast_start'] = float(this[1])
        parameters['fast_stop']  = float(this[2])
        parameters['fast_steps'] = int(this[3])
        found['fast_motor'], found['fast_start'], found['fast_stop'], found['fast_steps'] = True, True, True, True
    else:
        try:
            this = config.get('scan', 'fast').split()
            parameters['fast_motor'] = this[0]
            parameters['fast_start'] = float(this[1])
            parameters['fast_stop']  = float(this[2])
            parameters['fast_steps'] = int(this[3])
            found['fast_motor'], found['fast_start'], found['fast_stop'], found['fast_steps'] = True, True, True, True
        except:
            parameters['fast_motor'] = None
            parameters['fast_start'] = None
            parameters['fast_stop']  = None
            parameters['fast_steps'] = None
    if 'slow' in kwargs:
        this = kwargs['slow'].split()
        parameters['slow_motor'] = this[0]
        parameters['slow_start'] = float(this[1])
        parameters['slow_stop']  = float(this[2])
        parameters['slow_steps'] = int(this[3])
        found['slow_motor'], found['slow_start'], found['slow_stop'], found['slow_steps'] = True, True, True, True
    else:
        try:
            this = config.get('scan', 'slow').split()
            parameters['slow_motor'] = this[0]
            parameters['slow_start'] = float(this[1])
            parameters['slow_stop']  = float(this[2])
            parameters['slow_steps'] = int(this[3])
            found['slow_motor'], found['slow_start'], found['slow_stop'], found['slow_steps'] = True, True, True, True
        except:
            parameters['slow_motor'] = None
            parameters['slow_start'] = None
            parameters['slow_stop']  = None
            parameters['slow_steps'] = None
            
    return parameters, found



def difference_data(uid1, uid2, tag):
    '''Given two UIDs, make a difference map.  The assumption here is that
    uid1 is the UID of the higher energy map, and uid2 is the UID of
    the lower energy (or baseline) map.  

    For example, consider an experiment mapping the Ce3+ and Ce4+
    content of an object.  You might measure one map below the Ce L3
    edge, one map at about 5726 eV (near a XANES peak associated with
    Ce3+), and one map around 5738 eV (near a XANES peak associated
    with Ce4+).  To get the Ce3+ map, uid1 would be the UID of the
    5726 eV map and uid2 would be the map from below the edge.

    This function will write 3 files to the "maps/" folder in the
    user's data folder:
    * <tag>.xlsx : The data as a simple spreadsheet that many plotting programs can ingest
    * <tag>.mat  : The data in a form that Matlab can ingest
    * <tag>.png  : A matplotlib image of the processed difference map

    parameters
    ==========
    uid1 : (str) the UID of the first mapping
    uid2 : (str) the UID of the baseline map
    tag  : (str) a string to use to identify this difference spectrum

    '''

    ## HIP1
    ## 4+ : b9da51fb-a74b-4e86-bf92-e53e01ec763c
    ## 3+ : 66672202-4d35-4e50-869a-9c6fef89cf84
    ## bkg: 7afeb391-782a-4240-a676-4373fdbee301
    
    ## get motor names and image shape
    motors = bmm_catalog[uid1].metadata['start']['motors']
    [nslow, nfast] = bmm_catalog[uid1].metadata['start']['shape']

    ## slurp in data
    print('Reading primary data set...')
    datatable1 = bmm_catalog[uid1].primary.read()
    print('Reading secondary data set...')
    datatable2 = bmm_catalog[uid2].primary.read()

    ## common arrays and I0 arrays
    slow = numpy.array(datatable1[motors[0]])
    fast = numpy.array(datatable1[motors[1]])
    i01  = numpy.array(datatable1['I0'])
    i02  = numpy.array(datatable2['I0'])

    ## grab the signal based on what is listed in 'detectors' in the start document
    if 'xs' in bmm_catalog[uid1].metadata['start']['detectors']:
        det_name = bmm_catalog[uid1].metadata['start']['plan_name'].split()[-1]
        det_name = det_name[:-1]
        z1 = numpy.array(datatable1[det_name+'1'])+numpy.array(datatable1[det_name+'2'])+numpy.array(datatable1[det_name+'3'])+numpy.array(datatable1[det_name+'4'])
        z2 = numpy.array(datatable2[det_name+'1'])+numpy.array(datatable2[det_name+'2'])+numpy.array(datatable2[det_name+'3'])+numpy.array(datatable2[det_name+'4'])
    elif 'noisy_det' in bmm_catalog[uid1].metadata['start']['detectors']:
        z1 = numpy.array(datatable1['noisy_det'])
        z2 = numpy.array(datatable2['noisy_det'])

    ## save map in xlsx format
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = tag
    ws1.append(('slow', 'fast', 'difference', 'normalized_1', 'normalized_2', 'signal_1', 'I0_1', 'signal_2', 'I0_2'))
    n1 = z1/i01
    n2 = z2/i02
    diff = n1 - n2
    for i in range(len(slow)):
        ws1.append((slow[i], fast[i], diff[i], n1[i], n2[i], z1[i], i01[i], z2[i], i02[i]))
    wb.save(filename=os.path.join(user_ns['BMMuser'].folder, 'maps', f'{tag}.xlsx'))
    print(f'wrote {user_ns["BMMuser"].folder}/maps/{tag}.xlsx')
    
    ## save map in matlab format 
    savemat(os.path.join(user_ns['BMMuser'].folder, 'maps', f'{tag}.mat'), {'label'        : tag,
                                                                            motors[0]      : list(slow),
                                                                            motors[1]      : list(fast),
                                                                            'difference'   : list(diff),
                                                                            'normalized_1' : list(n1),
                                                                            'normalized_2' : list(n2),
                                                                            'I0_1'         : list(i01),
                                                                            'signal_1'     : list(z1),
                                                                            'I0_2'         : list(i02),
                                                                            'signal_2'     : list(z2), })
    print(f'wrote {user_ns["BMMuser"]}/maps/{tag}.mat')

    ## make a pretty picture of the difference map
    zzz=diff.reshape(nfast, nslow)
    # grabbing the first nfast elements of x and every
    # nslow-th element of y is more reliable than 
    # numpy.unique due to float &/or motor precision issues

    plt.title(tag)
    plt.xlabel(f'fast axis ({motors[0]}) position (mm)')
    plt.ylabel(f'slow axis ({motors[1]}) position (mm)')
    plt.gca().invert_yaxis()  # plot an xafs_x/xafs_y plot upright
    plt.contourf(fast[:nfast], slow[::nslow], zzz, cmap=plt.cm.viridis)
    plt.colorbar()
    plt.show()
    plt.savefig(os.path.join(user_ns['BMMuser'].folder, 'maps', f'{tag}.png'))

    
    
def raster(inifile=None, **kwargs):
    '''Perform a raster scan of an object. Read parameters from an INI
    file or provide parameters as argument (overriding the content of
    the INI file).

    Plan logic:
    1. Read the INI file and do sanity checking of the parameters
    2. Move to an energy specified in the INI file or in the argument list
    3. If interactive, prompt the user to verify the parameters
    4. Record relevant metadata and snap pictures from all cameras
    5. Do an areascan
    6. Save scan data (I0 and ROIs) as xlsx and matlab
    7. Write a dossier

    '''
    def main_plan(inifile, **kwargs):

        verbose = False
        if 'verbose' in kwargs and kwargs['verbose'] is True:
            verbose = True

        if is_re_worker_active():
            BMMuser.prompt = False
            kwargs['force'] = True

        if verbose: verbosebold_msg('checking clear to start (unless force=True)')
        if 'force' in kwargs and kwargs['force'] is True:
            (ok, text, force) = (True, '', True)
        else:
            (ok, text) = suspenders.clear_to_start()
            if ok is False:
                error_msg('\n'+text)
                bold_msg('Quitting scan sequence....\n')
                yield from null()
                return
        
        _locked_dwell_time.quadem_dwell_time.settle_time = 0
        inifile = os.path.join(BMMuser.workspace, inifile)


        if inifile is None:
            error_msg('\nNo inifile specified\n')
            return(yield from null())
        if not os.path.isfile(inifile):
            error_msg('\ninifile does not exist\n')
            return(yield from null())

        kafka.close_plots()
        rid = str(uuid.uuid4())[:8]
        report(f'== starting raster scan', level='bold', slack=True, rid=rid)

        p, f = read_ini(inifile, **kwargs)
        kafka.message({'dossier': 'start', 'stub': p['filename']})
        kafka.message({'dossier' : 'set', 'rid': rid})

        fast  = user_ns[p['fast_motor']]
        slow  = user_ns[p['slow_motor']]

        if BMMuser.prompt:
            text  = '\n'
            addition = f'fast motor: {fast.name} from {p["fast_start"]} to {p["fast_stop"]} in {p["fast_steps"]} steps (current position={fast.position:7.3f})'
            text = text + addition.rstrip() + '\n'
            
            addition = f'slow motor: {slow.name} from {p["slow_start"]} to {p["slow_stop"]} in {p["slow_steps"]} steps (current position={slow.position:7.3f})'
            text = text + addition.rstrip() + '\n\n'
            for k in sorted(p.keys()):
                if 'slow' in k or 'fast' in k:
                    continue
                if k in ('url', 'cif', 'doi'):
                    continue
                addition = f'{k:13} : {p[k]}'
                text = text + addition.rstrip() + '\n'
            boxedtext(text, title='How does this look?', color='green')

            pngout  = f"{p['filename']}.png"
            basename = p['filename']
            seqnumber = 1
            if kafka.file_exists(filename=pngout, number=False):
                seqnumber = 2
                while kafka.file_exists(filename=os.path.join(BMMuser.folder, 'maps', f"{p['filename']}-{seqnumber:02d}.png"), number=False):
                    seqnumber += 1
                basename = "%s-%2.2d" % (p['filename'],seqnumber)
                pngout = os.path.join(BMMuser.folder, 'maps', f"{p['filename']}-{seqnumber:02d}.png")

            #pngout = os.path.basename(pngout)
            #xlsxout = os.path.join(BMMuser.folder, 'maps', f"{p['filename']}-{seqnumber:02d}.xlsx")
            #matout  = os.path.join(BMMuser.folder, 'maps', f"{p['filename']}-{seqnumber:02d}.mat")
            xlsxout = f"maps/{p['filename']}-{seqnumber:02d}.xlsx"
            matout  = f"maps/{p['filename']}-{seqnumber:02d}.mat"
            print(f'\nImage data to be written to {pngout}, .xlsx, and .mat')
            estimate = float(p['fast_steps'])*float(p['slow_steps']) * (float(p['dwelltime'])+0.43)
            minutes = int(estimate/60)
            #seconds = int(estimate - minutes*60)
            print(f'Rough time estimate: {minutes} min')
            
            
            print()
            action = animated_prompt('Begin raster scan? ' + PROMPTNC)
            if action != '':
                if action[0].lower() == 'n' or action[0].lower() == 'q':
                    yield from null()
                    return

        ## move to measurement energy
        yield from mv(dcm.energy, p['energy'])
                    
        ## gather up input data into a format suitable for the dossier
        with open(inifile, 'r') as fd: content = fd.read()
        output = re.sub(r'\n+', '\n', re.sub(r'\#.*\n', '\n', content)) # remove comment and blank lines
        clargs = textwrap.fill(str(kwargs), width=50) # .replace('\n', '<br>')
        BMM_log_info('starting raster scan using %s:\n%s\ncommand line arguments = %s' % (inifile, output, str(kwargs)))
        #BMM_log_info(motor_status())

        ## organize metadata for injection into database and XDI output
        bold_msg('gathering metadata')
        md = bmm_metadata(measurement   = p['mode'],
                          experimenters = BMMuser.experimenters,
                          edge          = p['edge'],
                          element       = p['element'],
                          edge_energy   = p['energy'],
                          direction     = 1,
                          scantype      = 'step',
                          channelcut    = True, # p['channelcut'],
                          mono          = 'Si(%s)' % dcm._crystal,
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
        
        kafka.message({'mkdir': os.path.join(proposal_base(), 'maps')})

            
        #if p['detector'].lower() in ('if', 'xs', 'xs1'):

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## snap photos
        if p['snapshots']:
            yield from dossier.cameras(p['folder'], p['filename'], md)
            
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## capture dossier metadata for start document
        md['_snapshots'] = {**dossier.cameras_md, 'pngout':pngout, 'xlsxout': xlsxout, 'matout': matout}

        md['Beamline']['energy'] = dcm.energy.position

        
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## show the metadata to the user
        ##display_XDI_metadata(md)

        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## populate the static html page for this scan 
        these_kwargs = {#'fast': fast, 'slow': slow,
                        'fast_motor': f'{fast.name} [{p["fast_start"]}:{p["fast_stop"]}], {p["fast_steps"]} steps',
                        'slow_motor': f'{slow.name} [{p["slow_start"]}:{p["slow_stop"]}], {p["slow_steps"]} steps',
                        'fast_init': f'{fast.position:7.3f}', 'slow_init': f'{slow.position:7.3f}',
                        'pccenergy' : dcm.energy.position,
                        'startdate' : BMMuser.date,
                        
        }
        with open(os.path.join(BMMuser.workspace, inifile)) as f:
            initext = ''.join(f.readlines())
        user_metadata = {**p, **these_kwargs, 'initext': initext, 'clargs': clargs, 'experimenters': BMMuser.experimenters}
        md['_user'] = user_metadata
        xdi = {'XDI': md}
        
        ## --*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--*--
        ## engage suspenders right before starting scan sequence
        if 'force' in kwargs and kwargs['force'] is True:
            force = True
            pass
        else:
            force = False
            suspenders.set_suspenders()
        uid = yield from areascan(p['detector'],
                                  slow, p['slow_start'], p['slow_stop'], p['slow_steps'],
                                  fast, p['fast_start'], p['fast_stop'], p['fast_steps'],
                                  pluck=False, force=force, dwell=p['dwelltime'],
                                  fname=pngout, contour=p['contour'], log=p['log'], md=xdi)
        #preserve_data(uid, f'{p["filename"]} {dcm.energy.position} eV', xlsxout, matout)

        thisuid = bmm_catalog[-1].metadata['start']['uid']  # not sure why this is necessary....
        kafka.message({'raster': True, 'uid': thisuid})

        kafka.message({'dossier' : 'set',
                       'folder'  : BMMuser.folder,
                       'uidlist' : [thisuid,],
                       })
        kafka.message({'dossier' : 'raster', })

        
    def cleanup_plan(inifile):
        suspenders.clear_suspenders()
        try:
            print('Finishing up after a raster scan')
            yield from mv(dossier.fast, float(dossier.fast_init), dossier.slow, float(dossier.slow_init))
            dossier.seqend = now('%A, %B %d, %Y %I:%M %p')
            how = 'finished  :tada:'
            try:
                if 'primary' not in bmm_catalog[-1].metadata['stop']['num_events']:
                    how = '*stopped*'
                elif bmm_catalog[-1].metadata['stop']['num_events']['primary'] != bmm_catalog[-1].metadata['start']['num_points']:
                    how = '*stopped*'
            except:
                how = '*stopped*'
            report(f'== Raster scan {how}', level='bold', slack=True)
            try:
                htmlout = dossier.raster_dossier()
            except Exception as E:
                error_msg('Failed to write dossier.  Here is the exception message:')
                print(E)
                htmlout, prjout, pngout = None, None, None
            if htmlout is not None:
                htmlout = dossier.raster_dossier()
                report('wrote dossier %s' % htmlout, 'bold')
        except:
            whisper('Quitting raster scan. Not returning to start position or writing dossier.')
        yield from resting_state_plan()



    RE, BMMuser, dcm, dwell_time = user_ns['RE'], user_ns['BMMuser'], user_ns['dcm'], user_ns['dwell_time']
    RE.msg_hook = None
    dossier = DossierTools()

    if is_re_worker_active():
        inifile = '/home/xf06bm/Data/bucket/raster.ini'
    if inifile is None:
        inifile = present_options('ini')
    if inifile is None:
        return(yield from null())
    if inifile[-4:] != '.ini':
        inifile = inifile+'.ini'
    yield from finalize_wrapper(main_plan(inifile, **kwargs), cleanup_plan(inifile))
    RE.msg_hook = BMM_msg_hook
        
