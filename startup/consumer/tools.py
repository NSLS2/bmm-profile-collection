import os, datetime, emojis, re, configparser, numpy, time
from lmfit.models import StepModel, RectangleModel
from matplotlib import get_backend
import matplotlib
import matplotlib.pyplot as plt

ELEMENTS = [
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
    "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
    "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
    "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
    "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
    "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm",
    "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds",
    "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og"
]
pattern8 = r'\b(' + '|'.join(sorted(ELEMENTS, key=lambda x: -len(x))) + r')8\b'
element_regex8 = re.compile(pattern8)
pattern1 = r'\b(' + '|'.join(sorted(ELEMENTS, key=lambda x: -len(x))) + r')1\b'
element_regex1 = re.compile(pattern1)

startup_dir = os.path.dirname(os.path.dirname(__file__))
cfile = os.path.join(startup_dir, "BMM_configuration.ini")
profile_configuration = configparser.ConfigParser(interpolation=None)
profile_configuration.read_file(open(cfile))


BMM = profile_configuration.get('services', 'bmm')
PROPOSALS = profile_configuration.get('services', 'proposals')

import redis
from redis_json_dict import RedisJSONDict
nsls2_redis = profile_configuration.get('services', 'nsls2_redis')
redis_client = redis.Redis(host=nsls2_redis)

bmm_redis = profile_configuration.get('services', 'bmm_redis')
rkvs = redis.Redis(host=bmm_redis, port=6379, db=0)

#startup_dir = '/nsls2/data/bmm/shared/config/bluesky/profile_collection/startup/'


DATA_SECURITY = True

def experiment_folder(catalog, uid):

    facility_dict = RedisJSONDict(redis_client=redis_client, prefix='xas-')
    if 'data_session' in catalog[uid].metadata['start']:
        proposal = catalog[uid].metadata['start']['data_session'] #[5:]
    else:
        proposal = facility_dict['xas-data_session']
    if 'XDI' in catalog[uid].metadata['start'] and 'Facility' in catalog[uid].metadata['start']['XDI']:
        cycle = catalog[uid].metadata['start']['XDI']['Facility']['cycle']
    else:
        if 'xas_cycle' in facility_dict:
            cycle = facility_dict['xas-cycle']
        else:
            cycle = facility_dict['cycle']

    if DATA_SECURITY:
        folder    = os.path.join(PROPOSALS, cycle, f'{proposal}')
    else:
        proposal  = catalog[uid].metadata['start']['XDI']['Facility']['SAF']
        startdate = catalog[uid].metadata['start']['XDI']['_user']['startdate']
        folder = os.path.join(BMM, 'XAS', cycle, str(proposal), startdate)
    #print(f'folder is {folder}')
    return folder

def file_resource(catalog, uid):
    '''Dig through the documents for this uid to find the resource
    documents.  Make a list of fully resolved file paths pointed to in
    the resource documents.

    At BMM, these will point to files in the proposal assets folder.
    '''
    docs = catalog[uid].documents()
    found = []
    for d in docs:
        if d[0] == 'resource':
            this = os.path.join(d[1]['root'], d[1]['resource_path'])
            if '_%d' in this or re.search(r'%\d\.\dd', this) is not None:
                this = this % 0
            found.append(this)
    return found



def echo_slack(text='', img=None, icon='message', rid=None, measurement='xafs'):
    facility_dict = RedisJSONDict(redis_client=redis_client, prefix='xas-')
    base   = os.path.join(PROPOSALS, facility_dict['cycle'], facility_dict['data_session'])
    rawlogfile = os.path.join(base, 'dossier', '.rawlog')
    rawlog = open(rawlogfile, 'a')
    rawlog.write(message_div(text, img=img, icon=icon, rid=rid, measurement=measurement))
    rawlog.close()

    with open(os.path.join(startup_dir, 'tmpl', 'messagelog.tmpl')) as f:
        content = f.readlines()

    with open(rawlogfile, 'r') as fd:
        allmessages = fd.read()

    messagelog = os.path.join(base, 'dossier', 'messagelog.html')
    o = open(messagelog, 'w')
    o.write(''.join(content).format(text = allmessages, channel = 'BMM #beamtime'))
    o.close()

# this bit of html+css is derived from https://www.w3schools.com/howto/howto_css_chat.asp
def message_div(text='', img=None, icon='message', rid=None, measurement='xafs'):
    if measurement == 'raster':
        folder = 'maps'
    elif measurement == 'xrf':
        folder = 'XRF'
    else:
        folder = 'snapshots'

    if icon == 'message':
        avatar = 'message.png'
        image  = ''
        words  = f'<p>{emojis.encode(text)}</p>'
    elif icon == 'plot':
        avatar = 'plot.png'
        image  = f'<br><a href="../{folder}/{img}"><img class="left" src="../{folder}/{img}" style="height:240px;max-width:320px;width: auto;" alt="" /></a>'
        words  = f'<span class="figuretitle">{text}</span>'
    elif icon == 'camera':
        avatar = 'camera.png'
        image  = f'<br><a href="../{folder}/{img}"><img class="left" src="../{folder}/{img}" style="height:240px;max-width:320px;width: auto;" alt="" /></a>'
        words  = f'<span class="figuretitle">{text}</span>'
    else:
        return

    thisrid, clss, style = '', 'left', ''
    if rid is None:
        avatar = 'blank.png'
    elif rid is True:
        clss = 'top'
        style = ' style="border-top: 1px solid #000;"'  # horizontal line to demark groupings of comments
    else:
        thisrid = f' id="{rid}"'
        clss = 'top'
        style = ' style="border-top: 1px solid #000;"'

    this = f'''    <div class="container"{thisrid}{style}>
      <div class="left"><img src="{avatar}" style="width:30px;" /></div>
      <span class="time-right">{datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")}</span>
      {words}{image}
    </div>
'''
    return this





def next_index(folder, stub):
    '''Find the next numeric filename extension for a filename stub in folder.'''
    listing = os.listdir(folder)
    r = re.compile(re.escape(stub) + r'\.\d+')
    results = sorted(list(filter(r.match, listing)))
    if len(results) == 0:
        answer = 1
    else:
        answer = int(results[-1][-3:]) + 1
    rkvs.set('BMM:next_index', answer)
    print(f"Next index for {stub} in {folder} is {answer}.")


def file_exists(folder, filename, start, stop, number):
    '''Return true is a file of the supplied name exists in the supplied folder.'''
    target = os.path.join(folder, filename)
    found, text = False, []
    if number is True:
        for i in range(start, stop+1, 1):
            this = f'{target}.{i:03d}'
            #print(this)
            if os.path.isfile(this):
                found = True
                text.append(f'{filename}.{i:03d}')
    else:
        if os.path.isfile(target):
            found = True
            text.append(filename)


    if found is True:
        rkvs.set('BMM:file_exists', 'true')
        print(f"{', '.join(text)} found in {folder}.")
    else:
        rkvs.set('BMM:file_exists', 'false')
        print(f'"{filename}" not found in {folder} in range {start} - {stop}.')


from scipy.ndimage import center_of_mass
def com(signal):
    '''Return the center of mass of a 1D array. This is used to find the
    center of rocking curve and slit height scans.'''
    return int(center_of_mass(signal)[0])
def peak(signal):
    '''Return the index of the maximum of a 1D array. This is used to find the
    center of rocking curve and slit height scans.'''
    return numpy.argmax(signal)


def peakfit(catalog=None, uid=None, motor=None, signal='I0', choice='peak', spinner=None, ga=None):

    if uid == 'last':
        uid = catalog[-1].metadata['start']['uid']
        print(f'last UID was {uid}')

    top = 0
    t  = catalog[uid].primary['data']

    if signal == 'I0':
        ylabel = 'I0'
        sig = numpy.array(t[signal])
    elif signal == 'It':
        ylabel = 'It/I0'
        sig = numpy.array(t[signal]) / numpy.array(t['I0'])
    elif signal == 'Ir':
        ylabel = 'Ir/It'
        sig = numpy.array(t[signal]) / numpy.array(t['It'])
    elif signal == 'If':
        ylabel = 'If/I0'
        fluo_detectors = catalog[uid].metadata['start']['detectors']
        el = ''
        if '1-element SDD' in fluo_detectors:
            for k in catalog[uid].primary['data'].keys():
                if element_regex8.match(k):
                    el = element_regex8.match(k).groups()[0]
                    break
            sig = numpy.array(t[el+'8']) / numpy.array(t['I0'])
        elif '4-element SDD' in fluo_detectors:
            for k in catalog[uid].primary['data'].keys():
                if element_regex1.match(k):
                    el = element_regex1.match(k).groups()[0]
                    break
            sig = (numpy.array(t[el+'1']) +
                   numpy.array(t[el+'2']) +
                   numpy.array(t[el+'3']) +
                   numpy.array(t[el+'4'])) / numpy.array(t['I0'])
        elif '7-element SDD' in fluo_detectors:
            for k in catalog[uid].primary['data'].keys():
                if element_regex1.match(k):
                    el = element_regex1.match(k).groups()[0]
                    break
            sig = (numpy.array(t[el+'1']) +
                   numpy.array(t[el+'2']) +
                   numpy.array(t[el+'3']) +
                   numpy.array(t[el+'4']) +
                   numpy.array(t[el+'5']) +
                   numpy.array(t[el+'6']) +
                   numpy.array(t[el+'7'])) / numpy.array(t['I0'])
    else:
        ylabel = signal
        sig = numpy.array(t[signal])

    positions = numpy.array(t[motor])
    if choice.lower() == 'com':
        position = com(sig)
        top      = positions[position]
    elif choice.lower() == 'fit':
        mod      = SkewedGaussianModel()
        pars     = mod.guess(sig, x=positions)
        out      = mod.fit(sig, pars, x=positions)
        whisper(out.fit_report(min_correl=0))
        out.plot()
        top      = out.params['center'].value
    else:
        position = peak(sig)
        top      = positions[position]

    rkvs.set('BMM:peakposition', top)
    print(f'*** peak found at {motor} position {top}')

    #if self.fig is not None:
    #    plt.close(self.fig.number)
    fig = plt.figure()
    ax = fig.gca()
    ax.plot(positions, sig)
    ax.scatter(top, sig.max(), s=160, marker='x', color='green')
    ax.set_facecolor((0.95, 0.95, 0.95))
    ax.set_xlabel(f'{motor} (mm)')
    ax.set_ylabel(ylabel)
    if spinner is not None:
        ax.set_title(f'{motor} scan, spinner {spinner}, center={top:.3f}')
    else:
        ax.set_title(f'{motor} scan, center={top:.3f}')

    ## gather the information needed for the glancing angle auto-alignment summary plot
    if ga is not None and ga.ongoing is True:  # i.e. if currently doing a ga auto-alignment
        if signal == 'It':
            ga.pitch_xaxis = list(positions)
            ga.pitch_data = list(sig)
            ga.pitch_amplitude = sig.max()
            ga.pitch_center = top
            ga.spinner = spinner
            ga.pitch_uid = uid
            rkvs.set('BMM:ga:pitch_uid', uid)
        elif signal == 'If':
            ga.fluo_uid       = uid
            rkvs.set('BMM:ga:fluo_uid', uid)
            ga.fluo_motor     = motor
            ga.fluo_center    = top
            ga.fluo_amplitude = sig.max()
            ga.spinner        = spinner
            ga.fluo_xaxis     = list(positions)
            ga.fluo_data      = list(sig)
            ga.complete       = True

def rectanglefit(catalog=None, uid=None, motor=None, signal='It', drop=None, aw=None):

    top = 0
    t  = catalog[uid].primary['data']
    positions = numpy.array(t[motor])
    signal = signal.capitalize()
    if signal == 'I0':
        sig = numpy.array(t[signal])
    elif signal == 'It':
        sig = numpy.array(t[signal]) / numpy.array(t['I0'])
    elif signal == 'Ir':
        sig = numpy.array(t[signal]) / numpy.array(t['It'])
    else:
        sig = numpy.array(t[signal])

    if drop is not None:
        positions = positions[:-drop]
        sig = sig[:-drop]

    #if float(sig[2]) > list(sig)[-2] :
    #    ss       = -(sig - sig[2])
    #else:
    ss       = sig - sig[2]

    mod    = RectangleModel(form='erf')
    pars   = mod.guess(ss, x=numpy.array(positions))
    out    = mod.fit(ss, pars, x=numpy.array(positions))
    print(out.fit_report(min_correl=0))

    target = out.params["midpoint"].value
    amplitude = abs(out.params['amplitude'].value)
    rkvs.set('BMM:peakposition', target)
    print(f'*** midpoint of rectangle scan found at {motor} position {target:.3f}')

    if get_backend().lower() != 'agg':
        fig = plt.figure()
        ax = fig.gca()
        ax.scatter(positions, ss, color='blue')
        ax.plot(positions, out.best_fit, color='red')
        ax.scatter(target, abs(amplitude), s=160, marker='x', color='green')
        ax.set_facecolor((0.95, 0.95, 0.95))
        ax.set_xlabel(f'{motor} (mm)')
        ax.set_ylabel(f'{signal}/I0 and error function rectangle')
        ax.set_title(f'fit to {motor} scan, center={out.params["midpoint"].value:.3f}')
        fig.canvas.manager.show()
        fig.canvas.flush_events()

    if aw is not None and aw.ongoing is True:  # i.e. if currently doing a find_slot()
        direction =  motor.split('_')[1]
        if direction == 'x':
            aw.x_xaxis = list(positions)
            aw.x_data = list(ss)
            aw.x_best_fit = list(out.best_fit)
            aw.x_center = target-aw.x_offset
            aw.x_amplitude = amplitude
            aw.x_detector = signal.lower()
        else:
            aw.y_xaxis = list(positions)
            aw.y_data = list(ss)
            aw.y_best_fit = list(out.best_fit)
            aw.y_center = target
            aw.y_amplitude = amplitude
            aw.y_detector = signal.lower()

def stepfit(catalog=None, uid=None, motor=None, signal='It', spinner=None, ga=None):

    if uid == 'last':
        uid = catalog[-1].metadata['start']['uid']
        print(f'last UID was {uid}')

    target = 0
    t  = catalog[uid].primary['data']
    positions = numpy.array(t[motor])
    backwards = False
    if float(positions[-1]) < float(positions[0]):
        backwards = True
    if signal.lower() in ('bicron', 'apd', 'srtuck', 'monitor'):
        signal = 'monitor'

    if signal == 'I0':
        sig = numpy.array(t[signal])
        thissig = 'I0'
    elif signal == 'It':
        sig = numpy.array(t[signal]) / numpy.array(t['I0'])
        thissig = 'It/I0'
    elif signal == 'Ir':
        sig = numpy.array(t[signal]) / numpy.array(t['It'])
        thissig = 'Ir/It'
    elif signal == 'monitor':
        sig = numpy.array(t[signal])
        thissig = 'monitor'
    elif signal == 'mythen_dir':
        sig = numpy.array(t['dir']) / numpy.array(t['dwti_dwell_time'])
        thissig = 'monitor'
    else:
        sig = numpy.array(t[signal])
        thissig = signal

    if signal == 'monitor' and backwards is True:
        fix = 0
    elif signal == 'monitor' and backwards is False:
        fix = 0
    else:
        fix = sig[2]

    invert = False
    if backwards is False and float(sig[2]) > list(sig)[-2]:
        invert = True
    if backwards is True and float(sig[2]) < list(sig)[-2]:
        invert = True
        
        
    if invert is True:
        ss     = -(sig - fix)
        inverted = 'inverted '
    else:
        ss     = sig - fix
        inverted    = ''

    mod    = StepModel(form='erf')
    pars   = mod.guess(ss, x=numpy.array(positions))
    out    = mod.fit(ss, pars, x=numpy.array(positions))
    print(out.fit_report(min_correl=0.2))

    target = out.params['center'].value
    rkvs.set('BMM:peakposition', target)
    print(f'*** centroid of step function found at {motor} position {target:.3f}')
    print(rkvs.get('BMM:peakposition').decode('utf8'))
    
    if get_backend().lower() != 'agg':
        fig = plt.figure()
        ax = fig.gca()
        ax.scatter(positions, ss, color='blue')
        ax.plot(positions, out.best_fit, color='red')
        ax.scatter(target, out.params['amplitude'].value/2, s=160, marker='x', color='green')
        ax.set_facecolor((0.95, 0.95, 0.95))
        ax.set_xlabel(f'{motor}')
        ax.set_ylabel(f'{inverted}{thissig} and error function')
        if spinner is not None:
            ax.set_title(f'fit to {motor} scan, spinner {spinner}, center={target:.3f}')
        else:
            ax.set_title(f'fit to {motor} scan, center={target:.3f}')
        fig.canvas.manager.show()
        fig.canvas.flush_events()
        #out.plot()


    ## gather the information needed for the glancing angle auto-alignment summary plot
    if ga is not None and ga.ongoing is True:  # i.e. if currently doing a ga auto-alignment
        rkvs.set('BMM:ga:xy_uid', uid)
        ga.linear_uid       = uid
        ga.linear_motor     = motor
        ga.linear_center    = target
        ga.linear_amplitude = out.params['amplitude'].value
        ga.spinner          = spinner
        ga.linear_xaxis     = list(positions)
        ga.linear_data      = list(ss)
        ga.linear_best_fit  = list(out.best_fit)
        ga.inverted         = inverted



def flatten_message(message):
    '''In the context of live plotting at BMM, we use a client subscribed
    to a kafka (or redis-queue or whatever) message bus. This
    subscribes to the channel that is sending documents from the
    bluesky process to Tiled, parsing these intercepted documents, and
    making live plots suitable for the experiment.

    In the olden days, before ophyd-async and the other new tools in
    the newer ecosystem, the event page contained a dictionary
    carrying the various scalars measured at each point in a scan.
    The relevant part of that dictionary looked something like this:

       {'data': {'4-element SDD_channel01_xrf': '50a7e60c-86cb-4cca-a6ec-4f02179f8936/32',
                 '4-element SDD_channel02_xrf': '50a7e60c-86cb-4cca-a6ec-4f02179f8936/33',
                 '4-element SDD_channel03_xrf': '50a7e60c-86cb-4cca-a6ec-4f02179f8936/34',
                 '4-element SDD_channel04_xrf': '50a7e60c-86cb-4cca-a6ec-4f02179f8936/35',
                 'Cu1': 515.8938686551099,
                 'Cu2': 618.4181279567646,
                 'Cu3': 546.3841773831881,
                 'Cu4': 550.379763033371,
                 'I0': 22.285218594551086,
                 'Ir': 0.050800561904907254,
                 'It': 0.2420261135101318,
                 'xafs_x': 176.499,
                 'xafs_x_user_setpoint': 176.49900000000002},
         
    That is, the scalars were represented as floats.

    With the newer documents, now named "event_page" rather than
    "event", the same part of the dictionary looks like this:

       {'data': {'4-element SDD_channel01_xrf': ['2ed9b28f-a5ad-4f71-8904-a6805c9990cf/56'],
                 '4-element SDD_channel02_xrf': ['2ed9b28f-a5ad-4f71-8904-a6805c9990cf/57'],
                 '4-element SDD_channel03_xrf': ['2ed9b28f-a5ad-4f71-8904-a6805c9990cf/58'],
                 '4-element SDD_channel04_xrf': ['2ed9b28f-a5ad-4f71-8904-a6805c9990cf/59'],
                 'Cu1': [687.4539025312253],
                 'Cu2': [694.5866652621469],
                 'Cu3': [718.6267339855536],
                 'Cu4': [755.6538311937474],
                 'I0': [22.276790831565854],
                 'Ir': [0.0002650260925293119],
                 'It': [0.0017896404266357488],
                 'xafs_x': [177.098],
                 'xafs_x_user_setpoint': [177.098]},

    That is, the scalars are now ones-element lists.

    At the time of this writing (March 2026), bsui produces documents
    with floats, while queueserver produces documents with one-element lists.

    The purpose of this utility function is to flatten the newer
    documents so that those dictionaries can be consumed by existing code.

    In the long haul, code should be rewritten to use the newer
    documents and a utility function might be written to accommodate
    the older messages.

    '''

    datablock = message['data']
    for k in datablock.keys():
        if type(datablock[k]) is list:
            datablock[k] = datablock[k][0]
    message['data'] = datablock
    return message
