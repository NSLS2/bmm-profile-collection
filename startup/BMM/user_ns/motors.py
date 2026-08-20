from ophyd.sim import SynAxis
from ophyd import EpicsMotor, EpicsSignalRO, EpicsSignal

from bmm_tools.tools.messages import *  # error_msg et al. + boxedtext
from bmm_tools.tools.reset_offset import reset_offset
from bmm_tools.devices.motors import FMBOEpicsMotor, XAFSEpicsMotor, VacuumEpicsMotor, EndStationEpicsMotor, EncodedEndStationEpicsMotor, EpicsMotorWithDial
from bmm_tools.devices.motors import define_XAFSEpicsMotor, define_EndStationEpicsMotor, define_EncodedEndStationEpicsMotor

from BMM.functions import run_report, examine_fmbo_motor_group

from BMM.user_ns.base import profile_configuration

import time
from rich import print as cprint

run_report(__file__, text='individual motor definitions')


TAB = '\t\t\t'

mcs8_motors = list()

## front end slits
print(f'{TAB}Front end slit motor group')
fe_slits_horizontal1 = EpicsMotor('FE:C06B-OP{Slt:1-Ax:Hrz}Mtr',      name='fe_slits_horizontal1')
fe_slits_incline1    = EpicsMotor('FE:C06B-OP{Slt:1-Ax:Inc}Mtr',      name='fe_slits_incline1')
fe_slits_o           = EpicsMotor('FE:C06B-OP{Slt:1-Ax:O}Mtr',        name='fe_slits_o')
fe_slits_t           = EpicsMotor('FE:C06B-OP{Slt:1-Ax:T}Mtr',        name='fe_slits_t')
fe_slits_horizontal2 = EpicsMotor('FE:C06B-OP{Slt:2-Ax:Hrz}Mtr',      name='fe_slits_horizontal2')
fe_slits_incline2    = EpicsMotor('FE:C06B-OP{Slt:2-Ax:Inc}Mtr',      name='fe_slits_incline2')
fe_slits_i           = EpicsMotor('FE:C06B-OP{Slt:2-Ax:I}Mtr',        name='fe_slits_i')
fe_slits_b           = EpicsMotor('FE:C06B-OP{Slt:2-Ax:B}Mtr',        name='fe_slits_b')
fe_slits_hsize       = EpicsSignalRO('FE:C06B-OP{Slt:12-Ax:X}size',   name='fe_slits_hsize')
fe_slits_vsize       = EpicsSignalRO('FE:C06B-OP{Slt:12-Ax:Y}size',   name='fe_slits_vsize')
fe_slits_hcenter     = EpicsSignalRO('FE:C06B-OP{Slt:12-Ax:X}center', name='fe_slits_hcenter')
fe_slits_vcenter     = EpicsSignalRO('FE:C06B-OP{Slt:12-Ax:Y}center', name='fe_slits_vcenter')


def check_for_connection(m):
    if m.connected:
        return(True)
    disconnected_msg(f'{m.name} is not connected')
    for walk in m.walk_signals(include_lazy=False):
        if walk.item.connected is False:
            disconnected_msg(f'      {walk.item.name} is a disconnected PV')
    return(False)



## DM1
print(f'{TAB}FMBO motor group: dm1')
dm1_filters1 = define_XAFSEpicsMotor('XF:06BMA-BI{Fltr:01-Ax:Y1}Mtr', name='dm1_filters1')
dm1_filters2 = define_XAFSEpicsMotor('XF:06BMA-BI{Fltr:01-Ax:Y2}Mtr', name='dm1_filters2')
dm1list = [dm1_filters1, dm1_filters2]
mcs8_motors.extend(dm1list)
if 'XAFSEpicsMotor' in str(type(dm1_filters2)):
    dm1_filters2.llm.put(-52)
examine_fmbo_motor_group(dm1list)



## DM3
print(f'{TAB}FMBO motor group: dm2')  # it's not a big group... :/
dm2_fs = define_XAFSEpicsMotor('XF:06BMA-BI{Diag:02-Ax:Y}Mtr', name='dm2_fs')
if 'XAFSEpicsMotor' in str(type(dm2_fs)):
    dm2_fs.hvel_sp.put(0.0005)
mcs8_motors.append(dm2_fs)
examine_fmbo_motor_group([dm2_fs])



## DM3
print(f'{TAB}FMBO motor group: dm3')
#dm3_fs      = XAFSEpicsMotor('XF:06BM-BI{FS:03-Ax:Y}Mtr',   name='dm3_fs')
dm3_fs    = define_XAFSEpicsMotor('XF:06BM-BI{FS:03-Ax:Y}Mtr',   name='dm3_fs')
dm3_foils = define_XAFSEpicsMotor('XF:06BM-BI{Fltr:01-Ax:Y}Mtr', name='dm3_foils')
dm3_bct   = define_XAFSEpicsMotor('XF:06BM-BI{BCT-Ax:Y}Mtr',     name='dm3_bct')
dm3_bpm   = define_XAFSEpicsMotor('XF:06BM-BI{BPM:1-Ax:Y}Mtr',   name='dm3_bpm')

dm3list = [dm3_fs, dm3_foils, dm3_bct, dm3_bpm]
mcs8_motors.extend(dm3list)
examine_fmbo_motor_group(dm3list)

# make sure these motors are connected before trying to do things with them
if 'XAFSEpicsMotor' in str(type(dm3_fs)):
    dm3_fs.llm.put(-75)
    dm3_fs.hlm.put(56)
    dm3_fs.hvel_sp.put(0.05)

if 'XAFSEpicsMotor' in str(type(dm3_bct)):
    #dm3_bct.velocity.put(0.3)  # slowed down this motor to avoid rattling noise 17 December, 2024
    #dm3_bct.acceleration.put(0.25)
    #dm3_bct.hvel_sp.put(0.05)
    dm3_bct.llm.put(-60)
    dm3_bct.hlm.put(65)

if 'XAFSEpicsMotor' in str(type(dm3_bpm)):
    dm3_bpm.hvel_sp.put(0.05)

if 'XAFSEpicsMotor' in str(type(dm3_foils)):
    dm3_foils.llm.put(-25)
    dm3_foils.hlm.put(45)
    dm3_foils.hvel_sp.put(0.05)





    
## XAFS stages
print(f'{TAB}XAFS stages motor group')
#xafs_wheel  = xafs_rotb  = EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:RotB}Mtr',  name='xafs_wheel')
#xafs_roth   = define_EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:RotH}Mtr',  name='xafs_roth')
xafs_rots   = define_EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:RotS}Mtr',  name='xafs_rots')
#xafs_det    = xafs_lins  = define_EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:LinS}Mtr',  name='xafs_det')
xafs_detx   = xafs_det   = EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:Tbl_XD}Mtr',  name='xafs_detx')
xafs_refy   = define_EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:LinXS}Mtr', name='xafs_refy')
xafs_refx   = define_EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:RefX}Mtr', name='xafs_refx') 
xafs_adx = define_EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:LinX}Mtr',  name='xafs_adx')
xafs_ady = define_EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:LinY}Mtr',  name='xafs_ady')
xafs_roll   = define_EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:Pitch}Mtr', name='xafs_roll')  # note: the way this stage gets mounted, the
xafs_pitch  = define_EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:Roll}Mtr',  name='xafs_pitch') # EPICS names are swapped.  sigh....

xafs_garot = xafs_mtr8  = define_EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:Mtr8}Mtr',  name='xafs_garot') # EPICS names are swapped.



## MC09 stages -- stages with encoders and limit/home indicators
print(f'{TAB}XAFS stages motor group, encoded')
xafs_dety  = define_EncodedEndStationEpicsMotor('XF:06BM-ES{MC:09-Ax:1}Mtr',  name='xafs_dety')
xafs_detz  = define_EncodedEndStationEpicsMotor('XF:06BM-ES{MC:09-Ax:2}Mtr',  name='xafs_detz')
xafs_spare = define_EncodedEndStationEpicsMotor('XF:06BM-ES{MC:09-Ax:3}Mtr',  name='xafs_spare')
xafs_bsy   = define_EncodedEndStationEpicsMotor('XF:06BM-ES{MC:09-Ax:4}Mtr',  name='xafs_bsy')
xafs_bsx   = define_EncodedEndStationEpicsMotor('XF:06BM-ES{MC:09-Ax:5}Mtr',  name='xafs_bsx')
xafs_x     = define_EncodedEndStationEpicsMotor('XF:06BM-ES{MC:09-Ax:6}Mtr',  name='xafs_x')
xafs_y     = define_EncodedEndStationEpicsMotor('XF:06BM-ES{MC:09-Ax:7}Mtr',  name='xafs_y')

xafs_motors = [xafs_rots, xafs_refy, xafs_refx, xafs_rots,
               xafs_roll, xafs_pitch, xafs_garot, xafs_detx, xafs_adx, xafs_ady] 
homeable_xafs_motors = [xafs_dety, xafs_detz, xafs_spare, xafs_bsy, xafs_bsx, xafs_x, xafs_y]

xafs_motors.extend(homeable_xafs_motors)

def homed():
    normally_not_homed = ('dm1_filters1', 'dm1_filters2', 'dm2_fs',
                          'dm3_fs', 'dm3_foils', 'dm3_bpm', 'm1_yu',
                          'm1_ydo', 'm1_ydi', 'm1_xu', 'm1_xd',
                          'm2_bender', 'dcm_y')
    for m in mcs8_motors:
        if m.hocpl.get():
            print("%-12s : %s" % (m.name, m.hocpl.enum_strs[m.hocpl.get()]))
        elif m.name in normally_not_homed:
            cprint("[yellow]%-12s : %s[/yellow]" % (m.name, 'normally ' + m.hocpl.enum_strs[m.hocpl.get()].lower()))
        else:
            cprint("[red1]%-12s : %s[/red1]" % (m.name, m.hocpl.enum_strs[m.hocpl.get()]))
    for m in homeable_xafs_motors:
        if m.homed() == 'Homed':
            print("%-12s : %s" % (m.name, m.homed()))
        else:
            cprint("[red1]%-12s : %s[\red1]" % (m.name, m.homed()))

def ampen():
    for m in mcs8_motors:
        if m.ampen.get():
            print("%-12s : %s" % (m.name, warning_msg(m.ampen.enum_strs[m.ampen.get()])))
        else:
            print("%-12s : %s" % (m.name, m.ampen.enum_strs[m.ampen.get()]))
            

def amfe():
    bold_msg("%-12s : %s / %s" % ('motor', 'AMFE', 'AMFAE'))
    for m in mcs8_motors:
        if 'm1' in m.name:
            continue
        if m.amfe.get():
            fe  = warning_msg(m.amfe.enum_strs[m.amfe.get()])
        else:
            fe  = m.amfe.enum_strs[m.amfe.get()]
        if m.amfae.get():
            fae = warning_msg(m.amfae.enum_strs[m.amfae.get()])
        else:
            fae = m.amfae.enum_strs[m.amfae.get()]
        print("%-12s : %s / %s" % (m.name, fe, fae))
faults = amfe
            

def configure_xafs_y(load='light'):
    '''Configure speeds and accelerations for heavy and light load.

    argument is either 'light' or 'heavy'.  Default is 'light'.
    '''
    if load == 'light':
        print('\t\t\tConfiguring xafs_y for light load')
        settings = {'VMAX': 4,
                    'VELO': 3,
                    'JVEL': 3,
                    'ACCL': 0.1,
                    'JAR' : 10
        }
    else:
        print('\t\t\tConfiguring xafs_y for heavy load')
        settings = {'VMAX': 1,
                    'VELO': 1,
                    'JVEL': 1,
                    'ACCL': 0.1,
                    'JAR' : 10
        }
        
    for k, v in settings.items():
        toss = EpicsSignal(f'XF:06BM-ES{{MC:09-Ax:7}}Mtr.{k}', name='toss')
        toss.put(v)

WITH_DISPLEX = profile_configuration['experiments']['displex'] # False
if WITH_DISPLEX is True:
    configure_xafs_y('heavy')
else:
    configure_xafs_y('light')


# def reset_offset(motor=None, newpos=0):
#     current_offset  = motor.user_offset.get()
#     current_position = motor.position
#     new_offset = -1 * current_position + current_offset + newpos
#     motor.user_offset.put(new_offset)
    
