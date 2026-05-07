from ophyd import EpicsSignalRO

from bmm_tools.tools.messages import *  # error_msg et al. + boxedtext

from BMM.functions import run_report

from bmm_tools.devices.utilities import Vacuum, TCG, FEVac, GateValve, Thermocouple, OneWireTC, BMM_DIWater, Rack

run_report(__file__, text='monitor utilities')

#############################
# beamline enabled/disabled #
#############################

try:
    bl_enabled = EpicsSignalRO('SR:C06-EPS{PLC:1}Sts:BM_BE_Enbl-Sts', name='enabled')
except:
    bl_enabled = 0

#####################
# state of shutters #
#####################

from BMM.user_ns.instruments import shb, bmps, idps

def ocd(text, signal, negate=False):
    '''Indicated open/close/disconnected state with suitable text coloring.
    '''
    outtext = text
    if 'ShutterTuple' in str(type(signal.get())):
        get = signal.get()[0]
        open_state = 'open         '
        closed_state = '[bold red]closed[/bold red]       '
    else:
        get = signal.get()
        open_state = 'enabled      '
        closed_state = '[bold red]disabled[/bold red]      '
    if negate:
        get = -get+1
    try:
        if signal.connected is False:
            outtext += '[bold magenta]disconnected[\bold magenta] '
        elif get == 1:
            outtext += open_state
        else:
            outtext += closed_state
    except:
        outtext += '[bold black]unavailable[/bold black]   '
    return outtext

    
def show_shutters():
    ena_text  = ocd('  Beamline: ', bl_enabled)
    bmps_text = ocd('  BMPS: ', bmps)
    idps_text = ocd('            IDPS: ', idps)
    shb_text  = ocd('            Photon Shutter: ', shb, True)
    return(ena_text + bmps_text + idps_text + shb_text)


##########################################################
# state of vacuum levels in the various vacuum sections  #
# the seven PDS vacuum sections report on Pirani and CCG #
# the XRD flight path is just a Pirani                   #
##########################################################


fev = FEVac('FE:C06B-VA{', name='FrontEndVacuum')

vac = [Vacuum('XF:06BMA-VA{FS:1',     name='Diagnostic Module 1'),
       Vacuum('XF:06BMA-VA{Mono:DCM', name='Monochromator'),
       Vacuum('XF:06BMA-VA{FS:2',     name='Diagnostic Module 2'),
       Vacuum('XF:06BMA-VA{Mir:2',    name='Focusing Mirror'),
       Vacuum('XF:06BMA-VA{Mir:3',    name='Harmonic Rej. Mirror'),
       Vacuum('XF:06BMB-VA{BT:1',     name='Transport Pipe'),
       Vacuum('XF:06BMB-VA{FS:3',     name='Diagnostic Module 3')]

flight_path = TCG('XF:06BMB-VA{FltPth:1', name='Flight Path')

#Failed to connect to XF:06BMA-VA{Mono:DCM:OPS-BI{DCCT:1}I:Real-I


def show_vacuum():
    text  = ' Vacuum section       pressure    current\n'
    text += '==================================================\n'
    for v in vac:
        if float(v.current.get()) > 5e-4:
            text += '%-20s  %s    %4s  mA\n' % (v.name, v._pressure(), v._current())
        else:
            text += '%-20s  %s    %4s μA\n' % (v.name, v._pressure(), v._current())
    text += '%-20s  %s\n' % (flight_path.name, flight_path._pressure())
    for i in range(1,7):
        text += 'Front end section %d   %s\n' % (i, fev._pressure(i))
    boxedtext(text, title=f"BMM vacuum", color='yellow')

############################
# state of gate valves     #
############################

from BMM.user_ns.instruments import fs1, ln2
gv = [GateValve('FE:C06B-VA{GV:1}',        name='FEGV1'),
      GateValve('FE:C06B-VA{GV:3}',        name='FEGV3'),
      GateValve('FE:C06B-VA{GV:2}',        name='FEGV2'),
      GateValve('XF:06BMA-VA{FS:1-GV:1}',  name='GV1'),
      GateValve('XF:06BMA-VA{BS:PB-GV:1}', name='GV2'),
      GateValve('XF:06BMA-VA{FS:2-GV:1}',  name='GV3'),
      GateValve('XF:06BMA-VA{Mir:2-GV:1}', name='GV4'),
      GateValve('XF:06BMA-VA{Mir:3-GV:1}', name='GV5'),
      GateValve('XF:06BMB-VA{BT:1-GV:1}',  name='GV6'),
      fs1, ln2]

def show_gate_valves():
    print('  Valve      state')
    print('=======================')
    for g in gv:
        print('  %-6s     %s' % (g.name, g._state()))

def open_valve(num):
    which = num + 2
    gv[which].open()

def close_valve(num):
    which = num + 2
    gv[which].close()
    
#############################################################
# thermocouples on photon delivery system, read through EPS #
#############################################################
        
tcs = [Thermocouple('XF:06BM-EPS-OP{Mir:1}T:1',              name = 'Mirror 1, inboard fin'),
       Thermocouple('XF:06BM-EPS-OP{Mir:1}T:2',              name = 'Mirror 1, disaster mask'),
       Thermocouple('XF:06BM-EPS-OP{Mir:1}T:3',              name = 'Mirror 1, outboard fin'),
       Thermocouple('XF:06BMA-OP{FS:1}T:1',                  name = 'First fluorescent screen'),
       Thermocouple('XF:06BMA-OP{Mono:DCM-Crys:1}T',         name = 'Si111 first crystal'),
       Thermocouple('XF:06BMA-OP{Mono:DCM-Crys:2}T',         name = 'Si311 first crystal'),
       Thermocouple('XF:06BMA-OP{Mono:DCM-Crys:1-Ax:R}T',    name = 'Compton shield'),
       Thermocouple('XF:06BMA-OP{Mono:DCM-Crys:2-Ax:R}T',    name = 'Second crystal roll'),
       Thermocouple('XF:06BMA-OP{Mono:DCM-Crys:2-Ax:P}T',    name = 'Second crystal pitch'),
       Thermocouple('XF:06BMA-OP{Mono:DCM-Crys:2-Ax:Perp}T', name = 'Second crystal perpendicular'),
       Thermocouple('XF:06BMA-OP{Mono:DCM-Crys:2-Ax:Para}T', name = 'Second crystal parallel'),
       Thermocouple('XF:06BMA-OP{Mir:2}T:1',                 name = 'Mirror 2 upstream'),
       Thermocouple('XF:06BMA-OP{Mir:2}T:2',                 name = 'Mirror 2 downstream'),
       Thermocouple('XF:06BMA-OP{Mir:3}T:1',                 name = 'Mirror 3 upstream'),
       Thermocouple('XF:06BMA-OP{Mir:3}T:2',                 name = 'Mirror 3 downstream'),
       #Thermocouple('XF:06BMA-OP{Fltr:1}T:1',                name = 'Filter assembly 1, slot 1'),
       #Thermocouple('XF:06BMA-OP{Fltr:1}T:3',                name = 'Filter assembly 1, slot 2'),
       #Thermocouple('XF:06BMA-OP{Fltr:1}T:5',                name = 'Filter assembly 1, slot 3'),
       #Thermocouple('XF:06BMA-OP{Fltr:1}T:7',                name = 'Filter assembly 1, slot 4'),
       #Thermocouple('XF:06BMA-OP{Fltr:1}T:2',                name = 'Filter assembly 2, slot 1'),
       #Thermocouple('XF:06BMA-OP{Fltr:1}T:4',                name = 'Filter assembly 2, slot 2'),
       #Thermocouple('XF:06BMA-OP{Fltr:1}T:6',                name = 'Filter assembly 2, slot 3'),
       #Thermocouple('XF:06BMA-OP{Fltr:1}T:8',                name = 'Filter assembly 2, slot 4'),
       Rack('XF:06BM-CT{RG:A1-RTC:1}', name = 'Rack A'),
       Rack('XF:06BM-CT{RG:B1-RTC:1}', name = 'Rack B'),
       Rack('XF:06BM-CT{RG:C1-RTC:1}', name = 'Rack C1'),
       Rack('XF:06BM-CT{RG:C2-RTC:1}', name = 'Rack C2'),
       Rack('XF:06BM-CT{RG:C3-RTC:1}', name = 'Rack C3'),
   ]


###################################################################
# One-Wire network of temperature sensors placed around the mono  #
###################################################################


monotc_inboard       = OneWireTC('XF:6BMA{SENS:001}T', name='monotc_inboard')
monotc_upstream_high = OneWireTC('XF:6BMA{SENS:002}T', name='monotc_upstream_high')
monotc_downstream    = OneWireTC('XF:6BMA{SENS:003}T', name='monotc_downstream')
monotc_upstream_low  = OneWireTC('XF:6BMA{SENS:004}T', name='monotc_upstream_low')
    

def show_thermocouples():
    print('Thermocouple     Temperature')
    print('===================================')
    for t in tcs:
        print('  %-28s     %s' % (t.name, t._state()))


################
# flow sensors #
################

bmm_di = BMM_DIWater('XF:06BMA-UT{DI}', name='DI Water')
bmm_di.dcm_flow.name = 'DCM DI flow'
bmm_di.dm1_flow.name = 'DM1 DI flow'
bmm_di.supply_pressure.name = 'DI supply pressure'
bmm_di.supply_temperature.name = 'DI supply temperature'
bmm_di.return_pressure.name = 'DI return pressure'
bmm_di.return_temperature.name = 'DI return temperature'
pbs_di_a = EpicsSignalRO('XF:06BM-PPS{DI}F:A1-I', name='PBS water flow A')
pbs_di_b = EpicsSignalRO('XF:06BM-PPS{DI}F:B1-I', name='PBS water flow B')

pcw_supply_temperature = EpicsSignalRO('XF:06BMA-PU{PCW}T:Supply-I', name='PCW supply temperature')
pcw_return_temperature = EpicsSignalRO('XF:06BMA-PU{PCW}T:Return-I', name='PCW return temperature')

foe_leak_detector = EpicsSignalRO('XF:06BMA-UT{LD:1}Alrm-Sts', name='FOE water leak detector')

def show_water():
    text  = '  [white]' + datetime.datetime.now().strftime('%A %d %B, %Y %I:%M %p') + '[white]\n\n'
    text += '  Sensor                            Value\n'
    text += ' ==============================================\n'
    for pv in (bmm_di.dm1_flow, bmm_di.dcm_flow,
               bmm_di.supply_pressure, bmm_di.supply_temperature, bmm_di.return_pressure, bmm_di.return_temperature, 
               pbs_di_a, pbs_di_b, pcw_return_temperature, pcw_supply_temperature):
        if pv.alarm_status.value:
            text += '  [bold red]%-28s     %.1f %s[/bold red]\n' % (pv.name, float(pv.get()), pv.describe()[pv.name]['units'])
        else:
            text += '  %-28s     %.1f %s\n' % (pv.name, float(pv.get()), pv.describe()[pv.name]['units'])
    if foe_leak_detector.get() > 0:
        text += '  %-28s     %s\n' % (foe_leak_detector.name, foe_leak_detector.enum_strs[foe_leak_detector.get()].replace('  ', ' '))
    else:
        text += '  [bold red]%-28s     %s[/bold red]\n' % (foe_leak_detector.name, foe_leak_detector.enum_strs[foe_leak_detector.get()].replace('  ', ' '))
    return text[:-1]


def sw():
    boxedtext(show_water(), title=f"BMM water", color='yellow')
        

    
###########################################################################
# pretty-print a summary of temperatures, valve states, and vacuum levels #
###########################################################################

import datetime
def show_utilities():
    text = '  [green]' + datetime.datetime.now().strftime('%A %d %B, %Y %I:%M %p') + '[/green]\n\n'
    text += show_shutters() + '\n\n'
    ltcs = len(tcs)
    lvac = len(vac)
    lgv  = len(gv)
    text += '  Thermocouple               Temperature         Valve   state          Vacuum section       pressure     current\n'
    text += ' ====================================================================================================================\n'
    for i in range(0,ltcs):
        info = False
        if 'pitch' in tcs[i].name or 'roll' in tcs[i].name: info = True

        if i < lvac and i < lgv:
            units = 'μA'
            if float(vac[i].current.get()) > 5e-4: units = 'mA'
            text += '  [white]%-28s[/white]     %s C        [white]%-5s[/white]   %s        [white]%-20s[/white]  %s    %s %s\n' % \
                    (tcs[i].name, tcs[i]._state(info=info), gv[i].name, gv[i]._state(), vac[i].name, vac[i]._pressure(), vac[i]._current(), units)

        elif i == lvac:         # flight path ... TCG class
            text += '  [white]%-28s[/white]     %s C        [white]%-5s[/white]   %s        [white]%-20s[/white]  %s\n' % \
                    (tcs[i].name, tcs[i]._state(info=info), gv[i].name, gv[i]._state(), flight_path.name, flight_path._pressure())

        elif i < lgv:
            text += '  [white]%-28s[/white]     %s C        [white]%-5s[/white]   %s\n' % \
                    (tcs[i].name, tcs[i]._state(info=info), gv[i].name, gv[i]._state())

        else:
            text += '  [white]%-28s[/white]     %s C\n' % (tcs[i].name, tcs[i]._state(info=info))
    return text[:-1]

def su():
    boxedtext(show_utilities(), title=f"BMM utilities", color='yellow')
