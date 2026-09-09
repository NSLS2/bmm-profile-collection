import sys, json
sys.path.append('/home/xf06bm/.ipython/profile_collection/startup')

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from lmfit.models import SkewedGaussianModel, RectangleModel
import numpy, os, h5py, xraylib, datetime, pandas
from scipy.interpolate import interp1d
from mendeleev import element

from larch_interface import Pandrosus, Kekropidai
from slack import img_to_slack, post_to_slack
from tools import profile_configuration, element_regex1, element_regex8, rkvs


# import redis
# bmm_redis = profile_configuration['services']['bmm_redis']
# rkvs = redis.Redis(host=bmm_redis, port=6379, db=0)

def finished(record):
    if 'num' in record.metadata['start']['plan_args']:  # 1D scan
        expected = record.metadata['start']['plan_args']['num']
    else:                       # 2D scan
        expected = record.metadata['start']['num_points']        
    executed = record.metadata['stop']['num_events']['primary']
    if expected != executed:    # scan did not run to completion
        return False
    return True

def checkhint(hint, which):
    ret = True
    if which not in hint:
        ret = False
    if which == 'timescan':     # try "count"
        if 'count' in hint:
            ret = True
    if which == 'areascan':     # try "grid_scan"
        if 'grid_scan' in hint:
            ret = True
    if ret is False:
        print(f'plot_{which}: hint mismatch, how did you get here?')
    return ret

def plot_linescan(bmm_catalog, uid):
    record = bmm_catalog[uid]
    if finished(record) is False: return
    hint = record.metadata['start']['plan_name']
    if checkhint(hint, 'linescan') is False: return
    detector, motor = hint.split()[1:]

    table = record.primary.read()

    if detector == 'It':
        signal, label, title = table['It']/table['I0'], 'It/I0', f'transmission vs. {motor}'
    elif detector == 'Ir':
        signal, label, title = table['Ir'], 'Ir', f'reference vs. {motor}'
    elif detector == 'I0':
        signal, label, title = table['I0'], 'I0', f'I0 vs. {motor}'
    elif detector == 'I0a':
        signal, label, title = table['I0a']/table['I0'], 'I0a', f'I0a & I0b vs. {motor}'
        signal2 = table['I0b']/table['I0']
    elif detector == 'I0b':
        signal, label, title = table['I0b']/table['I0'], 'I0b', f'I0a & I0b vs. {motor}'
        signal2 = table['I0a']/table['I0']
    elif detector == 'Iy':
        signal, label, title = table['Iy']/table['I0'], 'Iy/I0', f'yield vs. {motor}'
    elif detector == 'If' or detector == 'Xs':
        xs1, xs2, xs3, xs4 = rkvs.get('BMM:user:xs1'), rkvs.get('BMM:user:xs2'), rkvs.get('BMM:user:xs3'), rkvs.get('BMM:user:xs4')
        signal = (table[xs1]+table[xs2]+table[xs3]+table[xs4]) / table['I0']
        label = 'If/I0'
        title = f'4 element fluorescence vs. {motor}'
    elif detector == 'Xs1':
        xs8 = rkvs.get('BMM:user:xs8')
        signal = table[xs8] / table['I0']
        label = 'If/I0'
        title = f'1 element fluorescence vs. {motor}'


    # Tom's explanation for how to do multiple plots: https://stackoverflow.com/a/31686953
    fig = plt.figure()
    ax = fig.gca()
    ax.plot(table[motor], signal, label=label)
    if detector == 'I0a':
        ax.plot(table[motor], signal2, label='I0b')
    if detector == 'I0b':
        ax.plot(table[motor], signal2, label='I0a')
    ax.set_facecolor((0.95, 0.95, 0.95))
    ax.set_title(title)
    ax.set_xlabel(motor)
    ax.set_ylabel(label)
    plt.gca().legend()
    fig.canvas.manager.show()
    fig.canvas.flush_events() 
        
        
def plot_timescan(bmm_catalog, uid):
    record = bmm_catalog[uid]
    if finished(record) is False: return
    hint = record.metadata['start']['plan_name']
    if checkhint(hint, 'timescan') is False: return
    detector = hint.split()[1]

    table = record.primary.read()

    if detector == 'It':
        signal, label, title = table['It'], 'It', 'It time scan'
    elif detector == 'Transmission':
        signal, label, title = numpy.log(table['I0']/table['It']), 'ln(I0/It)', 'transmission time scan'
    elif detector == 'Ir':
        signal, label, title = table['Ir'], 'Ir', 'Ir time scan'
    elif detector == 'Reference':
        signal, label, title = numpy.log(table['It']/table['Ir']), 'ln(It/Ir)', 'reference time scan'
    elif detector == 'I0':
        signal, label, title = table['I0'], 'I0', 'I0 time scan'
    elif detector == 'Iy':
        signal, label, title = table['Iy'], 'Iy', 'Iy time scan'
    elif detector == 'Yield':
        signal, label, title = table['Iy']/table['I0'], 'Iy/I0', 'yield time scan'
    elif detector == 'Fluorescence' or detector == 'Xs':
        xs1, xs2, xs3, xs4 = rkvs.get('BMM:user:xs1'), rkvs.get('BMM:user:xs2'), rkvs.get('BMM:user:xs3'), rkvs.get('BMM:user:xs4')
        signal = (table[xs1]+table[xs2]+table[xs3]+table[xs4]) / table['I0']
        label  = 'If/I0'
        title  = '4 element fluorescence time scan'
    elif detector == 'If':
        xs1, xs2, xs3, xs4 = rkvs.get('BMM:user:xs1'), rkvs.get('BMM:user:xs2'), rkvs.get('BMM:user:xs3'), rkvs.get('BMM:user:xs4')
        signal = table[xs1]+table[xs2]+table[xs3]+table[xs4]
        label  = 'If'
        title  = '4 element If time scan'
    elif detector == 'Xs1':
        xs8    = rkvs.get('BMM:user:xs8')
        signal = table[xs8] / table['I0']
        label  = 'If/I0'
        title  = '1 element fluorescence time scan'


    # Tom's explanation for how to do multiple plots: https://stackoverflow.com/a/31686953
    fig = plt.figure()
    ax = fig.gca()
    ax.plot(table['time']-table['time'][0], signal)
    ax.set_facecolor((0.95, 0.95, 0.95))
    ax.set_title(title)
    ax.set_xlabel('time (sec)')
    ax.set_ylabel(label)
    fig.canvas.manager.show()
    fig.canvas.flush_events() 


def plot_rectanglescan(bmm_catalog, uid):
    record = bmm_catalog[uid]
    if finished(record) is False: return
    hint = record.metadata['start']['plan_name']
    if checkhint(hint, 'rectanglescan') is False:
        print('failed to parse hint for rectanglescan')
        return
    hints = hint.split()
    detector, motor, negated = hints[3], hints[2], hints[-1]

    table = record.primary.read()

    if detector.lower() == 'if':
        xs1 = rkvs.get('BMM:user:xs1').decode('utf-8')
        xs2 = rkvs.get('BMM:user:xs2').decode('utf-8')
        xs3 = rkvs.get('BMM:user:xs3').decode('utf-8')
        xs4 = rkvs.get('BMM:user:xs4').decode('utf-8')
        signal   = numpy.array((table[xs1]+table[xs2]+table[xs3]+table[xs4])/table['I0'])
    elif detector.lower() == 'it':
        signal   = numpy.array(table['It']/table['I0'])
    elif detector.lower() == 'ir':
        signal   = numpy.array(table['Ir']/table['It'])
    
    signal = signal - signal[0]
    if negated == 'negated':
        signal = -1 * signal

    pos      = numpy.array(table[motor])
    mod      = RectangleModel(form='erf')
    pars     = mod.guess(signal, x=pos)
    out      = mod.fit(signal, pars, x=pos)
    #print(out.fit_report(min_correl=0))
    out.plot(xlabel=motor, ylabel=detector)

def plot_areascan(bmm_catalog, uid):
    record = bmm_catalog[uid]
    if finished(record) is False: return
    hint = record.metadata['start']['plan_name']
    if checkhint(hint, 'areascan') is False: return
    detector, slow, fast, contour, log, energy = hint.split()[1:]
    pngout = record.metadata['start']['BMM_kafka']['pngout']
    nslow, nfast = record.metadata['start']['shape']
    try:
        fname = record.metadata['start']['BMM_kafka']['pngout']
    except:
        fname = None
        
    table = record.primary.read()
    
    x=numpy.array(table[fast])
    y=numpy.array(table[slow])
    xs1 = rkvs.get('BMM:user:xs1').decode('utf-8')
    xs2 = rkvs.get('BMM:user:xs2').decode('utf-8')
    xs3 = rkvs.get('BMM:user:xs3').decode('utf-8')
    xs4 = rkvs.get('BMM:user:xs4').decode('utf-8')
    if detector.lower() == 'noisy_det':
        z=numpy.array(table['noisy_det'])
    elif detector.lower() == 'it':
        z=numpy.array(table['It'])
    elif detector.lower() == 'xs':

        fluo_detectors = record.metadata['start']['detectors']
        el = ''
        if '1-element SDD' in fluo_detectors:
            for k in record.primary.read().keys():
                if element_regex8.match(k):
                    el = element_regex8.match(k).groups()[0]
                    break
            z = numpy.array(table[el+'8']) # / numpy.array(table['I0'])
        elif '4-element SDD' in fluo_detectors:
            for k in record.primary.read().keys():
                if element_regex1.match(k):
                    el = element_regex1.match(k).groups()[0]
                    break
            z = numpy.array(table[el+'1']) + numpy.array(table[el+'2']) + \
                numpy.array(table[el+'3']) + numpy.array(table[el+'4'])     # ) / numpy.array(table['I0'])
        elif '7-element SDD' in fluo_detectors:
            for k in record.primary.read().keys():
                if element_regex1.match(k):
                    el = element_regex1.match(k).groups()[0]
                    break
            z = numpy.array(table[el+'1']) +\
                numpy.array(table[el+'2']) +\
                numpy.array(table[el+'3']) +\
                numpy.array(table[el+'4']) +\
                numpy.array(table[el+'5']) +\
                numpy.array(table[el+'6']) +\
                numpy.array(table[el+'7'])      #) / numpy.array(table['I0'])
        #z=numpy.array(table[xs1]) + numpy.array(table[xs2]) + numpy.array(table[xs3]) + numpy.array(table[xs4])
    else:
        z=numpy.array(table[xs1]) + numpy.array(table[xs2]) + numpy.array(table[xs3]) + numpy.array(table[xs4])

    z=z.reshape(nslow, nfast)
    if log == 'True':
        z = numpy.log(z)
        
    fig = plt.figure()
    fig.set_facecolor((0.95, 0.95, 0.95))
    plt.title(f'{detector}     Energy = {energy}')
    plt.xlabel(f'fast axis ({fast}) position (mm)')
    plt.ylabel(f'slow axis ({slow}) position (mm)')
    plt.gca().invert_yaxis()  # plot an xafs_x/xafs_y plot upright
    if contour == 'True':
        plt.contourf(x[:nfast], y[::nfast], z, cmap=plt.cm.viridis)
    else:
        plt.pcolormesh(x[:nfast], y[::nfast], z, cmap=plt.cm.viridis)
    plt.colorbar()
    # if pngout is not None and pngout.strip() != '':
    #     plt.savefig(pngout)
    #     img_to_slack(pngout, measurement='raster')
    plt.show()


def plot_xafs(bmm_catalog, uid):
    record = bmm_catalog[uid]
    if finished(record) is False: return
    hint = record.start['plan_name']
    mode = hint.split()[1]
    if checkhint(hint, 'xafs') is False: return
    this = Pandrosus()
    (this.element, this.edge, this.folder, this.db) = (record.start['XDI']['Element']['symbol'],
                                                       record.start['XDI']['Element']['edge'],
                                                       '',
                                                       bmm_catalog)
    this.facecolor = (0.95, 0.95, 0.95)
    this.fetch(uid=uid, mode=mode)
    this.title = f"{metadata['filename']}  scan {metadata['count']}/{metadata['repetitions']}"
    this.triplot()
    

def wafer_plot(**kwargs):
    uid       = kwargs['uid']
    motor     = kwargs['motor']
    center    = kwargs['center']
    amplitude = kwargs['amplitude']
    xaxis     = kwargs['xaxis']
    data      = kwargs['data']
    best_fit  = kwargs['best_fit']

    direction = motor.split('_')[1]

    fig = plt.figure()
    ax = fig.gca()
    ax.scatter(xaxis, data, color='blue')
    ax.plot(xaxis, best_fit, color='red')
    ax.scatter(center, amplitude/2, s=160, marker='x', color='green')
    ax.set_facecolor((0.95, 0.95, 0.95))
    ax.set_xlabel(f'{motor} (mm)')
    ax.set_ylabel(f'It/I0 and error function')
    ax.set_title(f'Wafer edge: {direction} scan, center={center:.3f}')
    fig.canvas.manager.show()
    fig.canvas.flush_events() 
    
    
def mono_calibration_plot(**kwargs):
    found    = kwargs['found']
    ee       = kwargs['ee']
    tt       = kwargs['tt']
    mono     = kwargs['mono']
    dspacing = kwargs['dspacing']
    offset   = kwargs['offset']

    y1, y2 = 13.5, 12.9
    if mono == '311':
        y1, y2 = 2*y1, 2*y2
    
    ## cubic interpolation of tabulated edge energies
    xnew = numpy.linspace(min(ee),max(ee),100)
    f = interp1d(ee, tt, kind='cubic')
        
    fig = plt.figure()
    ax = fig.gca()
    ax.plot(xnew, f(xnew), label='tabulated')
    ax.plot(found, tt, 'ro', label='measured')
    ax.set_facecolor((0.95, 0.95, 0.95))
    ax.set_xlabel('energy (eV)')
    ax.set_ylabel('angle (degrees)')
    ax.set_title(f'Si({mono}) calibration curve')
    ax.text(12000, y1, f'd-spacing = {dspacing[0]:.8f} ± {dspacing[1]:.8f} Å', fontsize='small')
    ax.text(12000, y2, f'offset = {offset[0]:.5f} ± {offset[1]:.5f} degrees', fontsize='small')
    ax.legend(loc='upper right', shadow=True)
    fig.canvas.manager.show()
    fig.canvas.flush_events() 



def xdi_xrf_header(**kwargs):
    text  = ''
    text += f'# XDI/1.0 BlueSky/{bluesky_version}\n'
    #text += '# Scan.uid: %s\n'          % dataframe['start']['uid']
    #text += '# Scan.transient_id: %d\n' % dataframe['start']['scan_id']
    text +=  '# Beamline.name: BMM (06BM) -- Beamline for Materials Measurement\n'
    text +=  '# Beamline.xray_source: NSLS-II three-pole wiggler\n'
    text +=  '# Beamline.collimation: paraboloid mirror, 5 nm Rh on 30 nm Pt\n'
    text += f'# Beamline.focusing: {kwargs["m2state"]}\n'
    text += f'# Beamline.harmonic_rejection: {kwargs["m3state"]}\n'
    text += f'# Beamline.energy: {kwargs["energy"]} eV\n'
    text +=  '# Detector.fluorescence: SII Vortex ME4 (4-element silicon drift)\n'
    if kwargs["i0val"] is not None:
        text += f'# Detector.i0value: {kwargs["i0val"]} nA\n'
    if kwargs["sample_name"] is not None:
        text += f'# Sample.name: {kwargs["sample_name"]}\n'
    if kwargs["sample_prep"] is not None:
        text += f'# Sample.prep: {kwargs["sample_prep"]}\n'
    if kwargs["sample_x"] is not None:
        text += f'# Sample.x: {kwargs["sample_x"]}\n'
    if kwargs["sample_y"] is not None:
        text += f'# Sample.y: {kwargs["sample_y"]}\n'
    text += f'# Scan.end_time: {kwargs["scan_end"]}\n'
    text += f'# Scan.dwell_time: {kwargs["dwell_time"]} seconds\n'
    if kwargs["uid"] is not None:
        text += f'# Scan.uid: {kwargs["uid"]}\n'
    text +=  '# Facility.name: NSLS-II\n'
    text += f'# Facility.current: {kwargs["current"]} mA\n'
    text += f'# Facility.mode: {kwargs["ring_mode"]}\n'
    text += f'# Facility.cycle: {kwargs["cycle"]}\n'
    text += f'# Facility.GUP: {kwargs["gup"]}\n'
    text += f'# Facility.SAF: {kwargs["saf"]}\n'
    text +=  '# Column.1: energy (eV)\n'
    for i in range(kwargs["ncol"]) :
        text += f'# Column.{i+2}: MCA{i+1} (counts)\n'
    text += '# ==========================================================\n'
    text += '# energy '
    for i in range(kwargs["ncol"]) :
        text += f' MCA{i+1} '
    text += '\n'
    return text

    
    
def xrfat(**kwargs):
    '''Examine an XRF spectrum measured during a fluorescence XAFS scan.  

    This extracts an array from the data stored in the HDF5 file
    recorded during an XAFS scan and plots it for the user.

    arguments
    =========
    uid : UID string
      The UID of the XAFS scan from which to extract the XRF spectrum

    energy : list of int or float
      The incident energies at which to plot the XRF spectra. If
      energy is 0 the first data point will be displayed.  If energy
      is a negative integer, the point that many steps from the end of
      the scan will be used. If energy is a positive integer less than
      the length of the scan, the point that many steps from the start
      of the scan will be used.  Otherwise, the data point with energy
      closest in value to the given energy will be displayed.

    xrffile : str
      The filename stub for an output XDI-style file containing the
      displayed XRF spectrum. This will be written into the XRF folder
      in the user's data folder.  If missing, the .xdi extension will
      be added. (THIS CURRENTLY DOES NOT WORK.)

    add : bool 
      If True, plot the sum of detector channels, else plot each
      individual channel.

    only : int
      If 1, 2, 3, or 4, plot only that channel from the 4-element
      detector.  8 means to plot the sole channel of the single
      element detector.  This does not actually have to be specified
      for and XAFS measurement using the 1-element detector.  That the
      1-element was used will be gleaned from the scan metadata.

    xmax : float 
      The upper extent of the XRF plot is the specified energy plus
      this value.

    '''
    catalog = kwargs['catalog']
    uid     = kwargs['uid']
    energy  = kwargs['energy']
    xrffile = kwargs['xrffile']
    add     = kwargs['add']
    only    = kwargs['only']
    xmax    = kwargs['xmax']


    datatable = catalog[uid].primary.read() # ['data']
    
    record  = catalog[uid]
    # xafs    = record.primary.read()
    # docs    = record.documents()
    # for d in docs:
    #     if d[0] == 'resource':
    #         hfile = os.path.join(d[1]['root'], d[1]['resource_path'])
    #         #if '_%d' in this: hfile = this % 0  #  deal with image files
    #         break
    
    # #hfile = file_resource(uid)
    # f  = h5py.File(hfile,'r')
    el = record.metadata["start"]["XDI"]["Element"]["symbol"]
    ed = record.metadata["start"]["XDI"]["Element"]["edge"]
    

    is_7_elem, is_4elem, is_1elem = False, False, False
    if '4-element SDD' in record.metadata["start"]['detectors']:
        is_4elem = True
        ncol = 4
    elif '1-element SDD' in record.metadata["start"]['detectors']:
        is_1elem = True
        ncol = 1
    elif '7-element SDD' in record.metadata["start"]['detectors']:
        is_7elem = True
        ncol = 7
    else:
        print('The specified scan was not a fluorescence XAFS scan.')
        return()
        
    dcm = numpy.array(datatable['dcm_energy'])
    fig = plt.figure()
    ax = fig.gca()
    # thisname = record.metadata["start"]["XDI"]["Sample"]["name"]
    # if len(thisname) > 30:
    #     thisname = thisname[:30].strip() + ' ...'
    thisname = os.path.splitext(os.path.basename(record.metadata["start"]["XDI"]['_filename']))[0]
    title = f'{thisname} at '
    if len(energy) > 1:
        xrffile = None
    for i,e in enumerate(energy):
        if e <= 0:
            position = e
            en = float(dcm[position])
        elif e < dcm[0] and e < len(dcm):
            position = e
            en = float(dcm[position])
        elif e < dcm[0] and e > len(dcm):
            position = 0
            en = float(dcm[position])
        else:
            position = numpy.abs(dcm - e).argmin()
            en = float(dcm[position])

        if is_4elem is True:
            s1 = datatable['4-element SDD_channel01_xrf'][position]
            s2 = datatable['4-element SDD_channel02_xrf'][position]
            s3 = datatable['4-element SDD_channel03_xrf'][position]
            s4 = datatable['4-element SDD_channel04_xrf'][position]
        elif is_7elem is True:
            s1 = datatable['7-element SDD_channel01_xrf'][position]
            s2 = datatable['7-element SDD_channel02_xrf'][position]
            s3 = datatable['7-element SDD_channel03_xrf'][position]
            s4 = datatable['7-element SDD_channel04_xrf'][position]
            s5 = datatable['7-element SDD_channel05_xrf'][position]
            s6 = datatable['7-element SDD_channel06_xrf'][position]
            s7 = datatable['7-element SDD_channel07_xrf'][position]
        else:
            s1 = datatable['1-element SDD_channel08_xrf'][position]
            add, only = False, 8
        ee = numpy.array(range(len(datatable['7-element SDD_channel07_xrf'][0])))*10

        title += f'{en:.1f}, '

        if i == 0:
            ax.set_xlabel('Energy  (eV)')
            ax.set_ylabel('counts')
            ax.grid(which='major', axis='both')
            ax.set_facecolor((0.95, 0.95, 0.95))
            ax.set_xlim(2500, en+xmax)
        if only is not None and only in (1, 2, 3, 4, 5, 6, 7, 8):
            if only == 1:
                ax.plot(ee, s1, label=f'channel1, {en:.1f} eV')
            elif only == 8:         #  1 element SDD
                ax.plot(ee, s1, label=f'channel8, {en:.1f} eV')
            elif only == 2:
                ax.plot(ee, s2, label=f'channel2, {en:.1f} eV')
            elif only == 3:
                ax.plot(ee, s3, label=f'channel3, {en:.1f} eV')
            elif only == 4:
                ax.plot(ee, s4, label=f'channel4, {en:.1f} eV')
            elif only == 5:
                ax.plot(ee, s5, label=f'channel5, {en:.1f} eV')
            elif only == 6:
                ax.plot(ee, s6, label=f'channel6, {en:.1f} eV')
            elif only == 7:
                ax.plot(ee, s7, label=f'channel7, {en:.1f} eV')
        elif add is True:
            ax.plot(ee, s1+s2+s3+s4, label=f'sum, {en:.1f} eV')
        else:
            if is_4elem is True:
                ax.plot(ee, s1, label='channel1')
                ax.plot(ee, s2, label='channel2')
                ax.plot(ee, s3, label='channel3')
                ax.plot(ee, s4, label='channel4')
            elif is_7elem is True:
                ax.plot(ee, s1, label='channel1')
                ax.plot(ee, s2, label='channel2')
                ax.plot(ee, s3, label='channel3')
                ax.plot(ee, s4, label='channel4')
                ax.plot(ee, s5, label='channel5')
                ax.plot(ee, s6, label='channel6')
                ax.plot(ee, s7, label='channel7')

        if i == 0:
            roicolor = '#aaaaaadd'
            z = element(el).atomic_number
            if ed.lower() == 'k':
                label = f'{el} Kα ROI'
                ke = (2*xraylib.LineEnergy(z, xraylib.KL3_LINE) + xraylib.LineEnergy(z, xraylib.KL2_LINE))*1000/3
                ax.axvline(x = ke,  color = roicolor, linewidth=1, label=label)
            elif ed.lower() == 'l3':
                label = f'{el} Lα ROI'
                ax.axvline(x = xraylib.LineEnergy(z, xraylib.L3M5_LINE)*1000, color = roicolor, linewidth=1, label=label)
            elif ed.lower() == 'l2':
                label = f'{el} Kβ1 ROI'
                ax.axvline(x = xraylib.LineEnergy(z, xraylib.L2M4_LINE)*1000, color = roicolor, linewidth=1, label=label)
            elif ed.lower() == 'l1':
                label = f'{el} Kβ3 ROI'
                ax.axvline(x = xraylib.LineEnergy(z, xraylib.L1M3_LINE)*1000, color = roicolor, linewidth=1, label=label)

        startup_dir = profile_configuration['services']['startup']        
        with open(os.path.join(startup_dir, 'rois.json'), 'r') as fl:
            js = fl.read()
        allrois = json.loads(js)

        ## highlight the ROI
        lower = allrois[el][ed.lower()]['low']  * 10
        upper = allrois[el][ed.lower()]['high'] * 10
        axis = plt.gca()
        ymin, ymax = axis.get_ylim()
        axis.add_patch(Rectangle((lower,ymin), upper-lower, ymax-ymin, facecolor=roicolor))                    
        
        ax.legend()
    title  = title[0:-2]
    title += ' eV'
    ax.set_title(title)

    if xrffile is not None:
        thistime = float(record.primary.read()['time'][position])
        kwargs = {'m2state' : record.metadata["start"]["XDI"]["Beamline"]["focusing"],
                  'm3state' : record.metadata["start"]["XDI"]["Beamline"]["harmonic_rejection"],
                  'energy' : round(energy, 1),
                  'i0val' : round(float(record.primary.read()['I0'][position]), 3),
                  'sample_name' : record.metadata["start"]["XDI"]["Sample"]["name"],
                  'sample_prep' : record.metadata["start"]["XDI"]["Sample"]["prep"],
                  'sample_x' : round(float(record.baseline.read()['xafs_x'][0]), 3),
                  'sample_y' : round(float(record.baseline.read()['xafs_y'][0]), 3),
                  'scan_end' : datetime.datetime.fromtimestamp(thistime).strftime("%Y-%m-%dT%H-%M-%S"),
                  'dwell_time' : float(record.primary.read()['dwti_dwell_time'][position]),
                  'uid' : uid + '  (this is the UID of the parent XAFS scan)',
                  'current' : record.metadata["start"]["XDI"]["Facility"]["current"],
                  'ring_mode' : record.metadata["start"]["XDI"]["Facility"]["mode"],
                  'cycle' : record.metadata["start"]["XDI"]["Facility"]["cycle"],
                  'gup' : record.metadata["start"]["XDI"]["Facility"]["GUP"],
                  'saf' : record.metadata["start"]["XDI"]["Facility"]["SAF"],
                  'ncol' : ncol,
            }
        handle = open(xrffile, 'w')
        handle.write(xdi_xrf_header(**kwargs))  # write XDI header
        if is_4elem:
            a = numpy.vstack((s1, s2, s3, s4))
            column_list = ['MCA1','MCA2','MCA3','MCA4']
        elif is_7elem:
            a = numpy.vstack((s1, s2, s3, s4, s5, s6, s7))
            column_list = ['MCA1','MCA2','MCA3','MCA4','MCA5','MCA7','MCA8']
        elif is_1elem:
            a = s1
            column_list = ['MCA1',]
        b=pandas.DataFrame(a.transpose(), index=ee, columns=column_list)
        handle.write(b.to_csv(sep=' ', header=False))  # write data table
        handle.flush()
        handle.close()
        print(f'Wrote XRF spectrum at {energy} eV to {xrffile}.')



def xrfplot(**kwargs):
    catalog = kwargs['catalog']
    uid     = kwargs['uid']
    energy  = kwargs['energy']
    add     = kwargs['add']
    only    = kwargs['only']
    return
