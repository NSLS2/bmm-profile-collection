
from ophyd import QuadEM, Component as Cpt, EpicsSignal, EpicsSignalRO, EpicsSignalWithRBV, Signal, Device
import datetime
import copy

from bluesky    import __version__ as bluesky_version
from ophyd      import __version__ as ophyd_version
from databroker import __version__ as databroker_version
import sys, re

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from BMM.user_ns.bmm         import BMMuser
from BMM.user_ns.dcm         import dcm
from BMM.user_ns.instruments import m2, m3, m2_bender, xafs_ref

class TC(Device):
    temperature = Cpt(EpicsSignal, 'T-I-I')


# class Ring(Device):
#     current    = Cpt(EpicsSignalRO, ':OPS-BI{DCCT:1}I:Real-I')
#     lifetime   = Cpt(EpicsSignalRO, ':OPS-BI{DCCT:1}Lifetime-I')
#     energy     = Cpt(EpicsSignalRO, '{}Energy_SRBend')
#     mode       = Cpt(EpicsSignalRO, '-OPS{}Mode-Sts', string=True)
#     filltarget = Cpt(EpicsSignalRO, '-HLA{}FillPattern:DesireImA')


## some heuristics for determining state of M2 and M3
def mirror_state():
    if m2.vertical.readback.get() > 0:
        m2state = 'not in use'
    else:
        m2state = 'torroidal mirror, 5 nm Rh on 30 nm Pt, pitch = %.2f mrad, bender = %d counts' % (7.0 - m2.pitch.readback.get(),
                                                                                                    int(m2_bender.user_readback.get()))
    if m3.lateral.readback.get() > 0:
        stripe =  'Pt stripe'
    else:
        stripe =  'Si stripe'
    if abs(m3.vertical.readback.get() + 1.5) < 0.1:
        m3state = 'not in use'
    else:
        m3state = 'flat mirror, %s, pitch = %.1f mrad relative to beam' % (stripe, 7.0 - m2.pitch.readback.get())
    return(m2state, m3state)



bmm_metadata_stub = {'Beamline': {'name'        : 'BMM (06BM) -- Beamline for Materials Measurement (NIST)',
                                  'collimation' : 'paraboloid mirror, 5 nm Rh on 30 nm Pt',
                                  'xray_source' : 'NSLS-II three-pole wiggler',
                              },
                     'Facility': {'name'        : 'NSLS-II',
                                  'energy'      : '3 GeV',},
}


def bmm_metadata(measurement   = 'transmission',
                 experimenters = '',
                 edge          = 'K',
                 element       = 'Fe',
                 edge_energy   = '7112',
                 direction     = 1,
                 scantype      = 'step',
                 channelcut    = True,
                 mono          = 'Si(111)',
                 i0_gas        = 'N2',
                 it_gas        = 'N2',
                 ir_gas        = 'N2',
                 sample        = 'Fe foil',
                 prep          = '',
                 stoichiometry = None,
                 mode          = 'transmission',
                 comment       = '',
                 ththth        = False,):
    '''
    fill a dictionary with BMM-specific metadata.  this will be stored in the <db>.start['md'] field

    Parameter
    ---------
    measurement : str
        'transmission' or 'fluorescence'
    edge : str
        'K', 'L3', 'L2', or 'L1'
    element : str
        one or two letter element symbol
    edge_energy : float
        edge energy used to constructing scan parameters
    direction : int
        1/0/-1, 1 for increasing, -1 for decreasing, 0 for fixed energy
    scan : str
        'step' or 'slew'
    channelcut : bool
        True/False, False for fixed exit, True for pseudo-channel-cut
    mono : ste
        'Si(111)' or 'Si(311)'
    i0_gas : str
        a string using N2, He, Ar, and Kr
    it_gas : str
        a string using N2, He, Ar, and Kr
    sample : str
        one-line sample description
    prep : str
        one-line explanation of sample preparation
    stoichiometry : str
        None or IUCr stoichiometry string
    mode : str
        transmission, fluorescence, reference
    comment : str
        user-supplied, free-form comment string
    ththth : bool
        True is measuring with the Si(333) reflection
    '''

    md                         = copy.deepcopy(bmm_metadata_stub)
    md['_mode']                = mode,
    #md['_kind']                = kind,
    md['_comment']             = comment,
    #md['_scantype']            = 'xafs step scan'
    if 'fixed' in scantype:
        md['_scantype']        = 'single-energy x-ray absorption detection'
    for k in ('Beamline', 'Element', 'Scan', 'Mono', 'Detector', 'Facility', 'Sample', 'Column'):
        if k not in md:
            md[k] = dict()
    md['Element']['edge']            = edge.capitalize()
    md['Element']['symbol']          = element.capitalize()
    if element.capitalize() in xafs_ref.mapping:
        md['Element']['reference']          = xafs_ref.mapping[element.capitalize()][2]
        md['Element']['reference_material'] = xafs_ref.mapping[element.capitalize()][3]
    else:
        md['Element']['reference']          = 'None'
        md['Element']['reference_material'] = 'None'
    
    md['Scan']['edge_energy']        = edge_energy
    md['Scan']['experimenters']      = experimenters
    md['Mono']['name']               = 'Si(%s)' % dcm._crystal
    md['Mono']['d_spacing']          = '%.7f' % (dcm._twod/2)
    md['Mono']['encoder_resolution'] = dcm.bragg.resolution.get()
    md['Mono']['angle_offset']       = dcm.bragg.user_offset.get()
    md['Detector']['I0']             = '25 cm ' + i0_gas + ', NSLS2 IC'
    md['Detector']['It']             = '25 cm ' + it_gas + ', NSLS2 IC'
    md['Detector']['Ir']             = '25 cm ' + ir_gas + ', NSLS2 IC'
    md['Facility']['GUP']            = BMMuser.gup
    md['Facility']['SAF']            = BMMuser.saf
    md['Facility']['cycle']          = BMMuser.cycle
    md['Sample']['name']             = sample
    md['Sample']['prep']             = prep
    #md['XDI']['Sample']['x_position']       = xafs_x.user_readback.get()
    #md['XDI']['Sample']['y_position']       = xafs_y.user_readback.get()
    #md['XDI']['Sample']['roll_position']    = xafs_roll.user_readback.get()
    ## what about pitch, adx/ady, bsx/bsy ???
    if stoichiometry is not None:
        md['Sample']['stoichiometry'] = stoichiometry

    if ththth:
        md['Mono']['name']            = 'Si(333)'
        md['Mono']['d_spacing']       = '%.7f' % (dcm._twod/6)
            
        
    (md['Beamline']['focusing'], md['Beamline']['harmonic_rejection']) = mirror_state()
    ## conda envs are now on Lustre with different naming semantics
    #collection = re.findall('collection[^/]*', sys.executable)[0]
    collection = sys.executable.split('/')[-3]
    python_version = sys.version.split(' ')[0]
    md['Beamline']['software'] = f'Bluesky {bluesky_version}, Ophyd {ophyd_version}, DataBroker {databroker_version}, Python {python_version}, {collection}'

    if direction > 0:
        md['Mono']['direction'] = 'increasing in energy'
    elif direction == 0:
        md['Mono']['direction'] = 'fixed in energy'
    else:
        md['Mono']['direction'] = 'decreasing in energy'

    if 'step' in scantype:
        md['Mono']['scan_type'] = 'step'
    elif 'fixed' in scantype:
        md['Mono']['scan_type'] = 'single energy'
    else:
        md['Mono']['scan_type'] = 'slew'

    # if channelcut is True:
    md['Mono']['scan_mode'] = 'pseudo channel cut'
    # else:
    #     md['Mono']['scan_mode'] = 'fixed exit'

    if 'fluo' in measurement or 'flou' in measurement or 'both' in measurement or 'xs' in measurement:
        if user_ns['xs'].name == '7-element SDD':
            md['Detector']['fluorescence'] = 'Hitachi Vortex ME7 (7-element silicon drift)'
        elif user_ns['xs'].name == '4-element SDD':
            md['Detector']['fluorescence'] = 'SII Vortex ME4 (4-element silicon drift)'
        elif user_ns['xs'].name == '1-element SDD':
            md['Detector']['fluorescence'] = 'SII Vortex ME1 (1-element silicon drift)'
        md['Detector']['deadtime_correction'] = 'Xspress3'
    #     md['Detector']['deadtime_correction'] = 'DOI: 10.1107/S0909049510009064'  # DEPRECATED
        
    if 'yield' in measurement:
        #md['Detector']['yield'] = 'Leeds multi-sample electron yield detector'
        md['Detector']['yield'] = 'electron yield detector'

    return md

def metadata_at_this_moment():
    '''Gather the sort of scan metadata that could change between scans
    in a scan sequence.  Return a dictionary.

    '''
    rightnow = dict()
    rightnow['Facility'] = dict()
    #rightnow['Mono']['first_crystal_temperature']  = float(first_crystal.temperature.get())
    #rightnow['Mono']['compton_shield_temperature'] = float(compton_shield.temperature.get())
    try:
        rightnow['Facility']['current']  = str(round(user_ns['ring'].current.get(), 1))
        rightnow['Facility']['energy']   = str(round(user_ns['ring'].energy.get()/1000., 1))
        rightnow['Facility']['mode']     = user_ns['ring'].mode.get()
    except Exception as E:
        print(E)
        rightnow['Facility']['current']  = '0'
        rightnow['Facility']['energy']   = '0'
        rightnow['Facility']['mode']     = 'Maintenance'
    if rightnow['Facility']['mode'] == 'Operations':
        rightnow['Facility']['mode'] = 'top-off'

    rightnow['Sample'] = dict()
    rightnow['Sample']['SDD_position'] = f"{user_ns['xafs_det'].position:.1f}"
    rightnow['Sample']['x'] = f"{user_ns['xafs_x'].position:.3f}"
    rightnow['Sample']['y'] = f"{user_ns['xafs_y'].position:.3f}"
    if 'glancing' in BMMuser.instrument.lower():
        rightnow['Sample']['pitch'] = f"{user_ns['xafs_pitch'].position:.3f}"
        
    #if BMMuser.extra_metadata is not None:
    #    rightnow['Sample'] = dict()
    #    rightnow['Sample']['extra_metadata'] = BMMuser.extra_metadata
    if 'linkam' in BMMuser.instrument.lower():
        rightnow['Sample']['temperature'] = user_ns['linkam'].readback.get()
    elif 'lakeshore' in BMMuser.instrument.lower():
        rightnow['Sample']['temperature_a'] = user_ns['lakeshore'].sample_a.get()
        rightnow['Sample']['temperature_b'] = user_ns['lakeshore'].sample_b.get()
    elif BMMuser.instrument.lower() == 'sample wheel':
        rightnow['Sample']['wheel_slot'] = user_ns['xafs_wheel'].slot_number()
    elif 'double' in BMMuser.instrument.lower():
        rightnow['Sample']['wheel_slot'] = user_ns['xafs_wheel'].slot_number()
        rightnow['Sample']['wheel_ring'] = user_ns['xafs_wheel'].slot_ring()
    elif 'glancing' in BMMuser.instrument.lower():
        rightnow['Sample']['spinner'] = user_ns['ga'].current()

    return rightnow



def display_XDI_metadata(dct):
    for family, ddd in dct.items():
        if family[0:1] == '_':
            continue
        if type(ddd) is dict:
            for item, value in ddd.items():
                print("\t{:30} {}".format(family+'.'+item+':', value))
    
