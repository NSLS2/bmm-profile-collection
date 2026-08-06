import time, json, os

from bmm_tools.tools.messages import error_msg, whisper

from BMM.functions import run_report, examine_fmbo_motor_group, examine_xafs_motor_group
from BMM.workspace import rkvs
from BMM.user_ns.base import profile_configuration

run_report(__file__, text='instrument definitions')

TAB = '\t\t\t'

WITH_LAKESHORE    = profile_configuration.getboolean('experiments', 'lakeshore') # False
WITH_LINKAM       = profile_configuration.getboolean('experiments', 'linkam') # True
WITH_ENCLOSURE    = profile_configuration.getboolean('experiments', 'enclosure') # False
WITH_SALTFURNACE  = profile_configuration.getboolean('experiments', 'saltfurnace') # False
WITH_RADIOLOGICAL = profile_configuration.getboolean('experiments', 'radiological') # False
if WITH_ENCLOSURE is True:
    from BMM.user_ns.motors import xafs_refy, xafs_refx
    run_report('\tAir Science enclosure')
    xafs_samx = xafs_refx
    xafs_samy = xafs_refy
    print(f'{TAB}defined xafs_samx/xafs_samy as xafs_refx/xafs_refy')
    
if WITH_SALTFURNACE is True:
    from BMM.user_ns.motors import xafs_refy, xafs_refx, xafs_det
    run_report('\tMolten salt furnace')
    xafs_detx = xafs_refx
    xafs_dety = xafs_refy
    xafs_detz = xafs_det
    print(f'{TAB}defined xafs_detx/xafs_dety/xafs_detz as xafs_refx/xafs_refy/xafs_det')

    
    
########################################################################
# Note: the use of SynAxis in this file is so that every motor-related #
# symbol gets set to `something' at startup.  This allows bsui to      #
# fully start and places the user at a fully-functional-for-BMM        #
# command line.                                                        #
#                                                                      #
# LOTS of things won't work correctly in this situation. For example,  #
# if M2 is disconnected, then anything that wants to touch M2 will not #
# work, e.g. `%w m2' or any kind of coordinated or non-coordinated     #
# motion.  But this allows one to use and develop BMM's bsui profile   #
# even with multiple motors disconnected.                              #
#                                                                      #
# The most common causes of a disconnected motor are an IOC that is    #
# not running or a controller that is powered down (or both).          #
########################################################################
from ophyd.sim import SynAxis
def wait_for_connection(thing):
    # give it a moment
    count = 0
    while thing.connected is False:
        count += 1
        time.sleep(0.5)
        if count > 10:
            break



## http://patorjk.com/software/taag/#p=display&f=Doom&t=MIRRORS

#################################################
# ___  ____________________ ___________  _____  #
# |  \/  |_   _| ___ \ ___ \  _  | ___ \/  ___| #
# | .  . | | | | |_/ / |_/ / | | | |_/ /\ `--.  #
# | |\/| | | | |    /|    /| | | |    /  `--. \ #
# | |  | |_| |_| |\ \| |\ \\ \_/ / |\ \ /\__/ / #
# \_|  |_/\___/\_| \_\_| \_|\___/\_| \_|\____/  #
#################################################


run_report('\tmirrors and tables')
from bmm_tools.devices.motors import XAFSEpicsMotor, Mirrors, XAFSTable, GonioTable, EndStationEpicsMotor
from BMM.user_ns.bmm import BMMuser
from BMM.user_ns.motors import mcs8_motors, xafs_motors

## collimating mirror
WITH_M1    = profile_configuration.getboolean('miscellaneous', 'with_m1') # False

if WITH_M1:
    print(f'{TAB}FMBO motor group: m1')
    m1 = Mirrors('XF:06BM-OP{Mir:M1-Ax:',  name='m1', mirror_length=556,  mirror_width=240)
    m1.vertical._limits = (-5.0, 5.0)
    m1.lateral._limits  = (-5.0, 5.0)
    m1.pitch._limits    = (-5.0, 5.0)
    m1.roll._limits     = (-5.0, 5.0)
    m1.yaw._limits      = (-5.0, 5.0)

    wait_for_connection(m1)



    if m1.connected is True:
        m1_yu     = XAFSEpicsMotor('XF:06BM-OP{Mir:M1-Ax:YU}Mtr',   name='m1_yu')
        m1_ydo    = XAFSEpicsMotor('XF:06BM-OP{Mir:M1-Ax:YDO}Mtr',  name='m1_ydo')
        m1_ydi    = XAFSEpicsMotor('XF:06BM-OP{Mir:M1-Ax:YDI}Mtr',  name='m1_ydi')
        m1_xu     = XAFSEpicsMotor('XF:06BM-OP{Mir:M1-Ax:XU}Mtr',   name='m1_xu')
        m1_xd     = XAFSEpicsMotor('XF:06BM-OP{Mir:M1-Ax:XD}Mtr',   name='m1_xd')
    else:
        m1_yu     = SynAxis(name='m1_yu')
        m1_ydo    = SynAxis(name='m1_ydo')
        m1_ydi    = SynAxis(name='m1_ydi')
        m1_xu     = SynAxis(name='m1_xu')
        m1_xd     = SynAxis(name='m1_xd')

    m1list = [m1_yu, m1_ydo, m1_ydi, m1_xu, m1_xd]
    mcs8_motors.extend(m1list)


## focusing mirror
print(f'{TAB}FMBO motor group: m2')
m2 = Mirrors('XF:06BMA-OP{Mir:M2-Ax:', name='m2', mirror_length=1288, mirror_width=240)
m2.vertical._limits = (-6.0, 8.0)
m2.lateral._limits  = (-2, 2)
m2.pitch._limits    = (-0.5, 5.0)
m2.roll._limits     = (-2, 2)
m2.yaw._limits      = (-1, 2)

wait_for_connection(m2)


#m2_yu, m2_ydo, m2_ydi, m2_xu, m2_xd, m2_bender = None, None, None, None, None, None
if m2.connected is True:
    m2_yu     = XAFSEpicsMotor('XF:06BMA-OP{Mir:M2-Ax:YU}Mtr',   name='m2_yu')
    m2_ydo    = XAFSEpicsMotor('XF:06BMA-OP{Mir:M2-Ax:YDO}Mtr',  name='m2_ydo')
    m2_ydi    = XAFSEpicsMotor('XF:06BMA-OP{Mir:M2-Ax:YDI}Mtr',  name='m2_ydi')
    m2_xu     = XAFSEpicsMotor('XF:06BMA-OP{Mir:M2-Ax:XU}Mtr',   name='m2_xu')
    m2_xd     = XAFSEpicsMotor('XF:06BMA-OP{Mir:M2-Ax:XD}Mtr',   name='m2_yxd')
    m2_bender = XAFSEpicsMotor('XF:06BMA-OP{Mir:M2-Ax:Bend}Mtr', name='m2_bender')
    m2_xu.velocity.put(0.05)
    m2_xd.velocity.put(0.05)
    m2.xu.user_offset.put(-0.2679)
    m2.xd.user_offset.put(1.0199)
else:
    m2_yu     = SynAxis(name='m2_yu')
    m2_ydo    = SynAxis(name='m2_ydo')
    m2_ydi    = SynAxis(name='m2_ydi')
    m2_xu     = SynAxis(name='m2_xu')
    m2_xd     = SynAxis(name='m2_xd')
    m2_bender = SynAxis(name='m2_bender')
m2list = [m2_yu, m2_ydo, m2_ydi, m2_xu, m2_xd, m2_bender]
mcs8_motors.extend(m2list)   
examine_fmbo_motor_group(m2list)


## harmonic rejection mirror
print(f'{TAB}FMBO motor group: m3')
m3 = Mirrors('XF:06BMA-OP{Mir:M3-Ax:', name='m3', mirror_length=667,  mirror_width=240)
m3.vertical._limits = (-11, 1)
m3.lateral._limits  = (-16, 16)
m3.pitch._limits    = (-6, 6)
m3.roll._limits     = (-2, 2)
m3.yaw._limits      = (-1, 1)

wait_for_connection(m3)

#m3_yu, m3_ydo, m3_ydi, m3_xu, m3_xd = None, None, None, None, None
if m3.connected is True:
    m3_yu     = XAFSEpicsMotor('XF:06BMA-OP{Mir:M3-Ax:YU}Mtr',   name='m3_yu')
    m3_ydo    = XAFSEpicsMotor('XF:06BMA-OP{Mir:M3-Ax:YDO}Mtr',  name='m3_ydo')
    m3_ydi    = XAFSEpicsMotor('XF:06BMA-OP{Mir:M3-Ax:YDI}Mtr',  name='m3_ydi')
    m3_xu     = XAFSEpicsMotor('XF:06BMA-OP{Mir:M3-Ax:XU}Mtr',   name='m3_xu')
    m3_xd     = XAFSEpicsMotor('XF:06BMA-OP{Mir:M3-Ax:XD}Mtr',   name='m3_xd')
    m3_xd.velocity.put(0.15)
    m3_xu.velocity.put(0.15)
    #m3.ydo.user_offset.put(-2.1705) #-0.37    
    #m3.ydi.user_offset.put(1.5599)  #-0.24
    m3.xd.user_offset.put(4.691) # fix yaw after January 2022 M3 intervention
    m3.xu.user_offset.put(0.647)
else:
    m3_yu     = SynAxis(name='m3_yu')
    m3_ydo    = SynAxis(name='m3_ydo')
    m3_ydi    = SynAxis(name='m3_ydi')
    m3_xu     = SynAxis(name='m3_xu')
    m3_xd     = SynAxis(name='m3_xd')
mcs8_motors.extend([m3_yu, m3_ydo, m3_ydi, m3_xu, m3_xd])
examine_fmbo_motor_group([m3_yu, m3_ydo, m3_ydi, m3_xu, m3_xd])



def kill_mirror_jacks():
    if m2.connected is True:
        yield from m2.kill_jacks()
    if m3.connected is True:
        yield from m3.kill_jacks()


## XAFS table
print(f'{TAB}XAFS table motor group')
xt = xafs_table = XAFSTable('XF:06BMA-BI{XAFS-Ax:Tbl_', name='xafs_table', mirror_length=1160,  mirror_width=558)
wait_for_connection(xafs_table)

if xafs_table.connected is True:
    xafs_yu  = EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:Tbl_YU}Mtr',  name='xafs_yu')
    xafs_ydo = EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:Tbl_YDO}Mtr', name='xafs_ydo')
    xafs_ydi = EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:Tbl_YDI}Mtr', name='xafs_ydi')
    #xafs_xu  = EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:Tbl_XU}Mtr',  name='xafs_xu')
    #xafs_xd  = EndStationEpicsMotor('XF:06BMA-BI{XAFS-Ax:Tbl_XD}Mtr',  name='xafs_xd')
else:
    xafs_yu     = SynAxis(name='xafs_yu')
    xafs_ydo    = SynAxis(name='xafs_ydo')
    xafs_ydi    = SynAxis(name='xafs_ydi')
    xafs_xu     = SynAxis(name='xafs_xu')
    xafs_xd     = SynAxis(name='xafs_xd')
    
xafs_motors.extend([xafs_yu, xafs_ydo, xafs_ydi]) #, xafs_xu, xafs_xd])

print(f'{TAB}Examine XAFS motor groups')
examine_xafs_motor_group(xafs_motors)

run_report('\tmirror trigonometry')
from BMM.mirror_trigonometry import move_m2, move_m3


###################################
#  _____ _     _____ _____ _____  #
# /  ___| |   |_   _|_   _/  ___| #
# \ `--.| |     | |   | | \ `--.  #
#  `--. \ |     | |   | |  `--. \ #
# /\__/ / |_____| |_  | | /\__/ / #
# \____/\_____/\___/  \_/ \____/  #
###################################
                               

run_report('\tslits')
from bmm_tools.devices.slits import StandardSlits, GonioSlits

## DM3
print(f'{TAB}FMBO motor group: slits3')
sl = slits3 = StandardSlits('XF:06BM-BI{Slt:02-Ax:',  name='slits3')
slits3.nominal = [7.0, 1.0, 0.0, 0.0]
wait_for_connection(slits3)

if slits3.connected is True:
    dm3_slits_o = XAFSEpicsMotor('XF:06BM-BI{Slt:02-Ax:O}Mtr',  name='dm3_slits_o')
    dm3_slits_i = XAFSEpicsMotor('XF:06BM-BI{Slt:02-Ax:I}Mtr',  name='dm3_slits_i')
    dm3_slits_t = XAFSEpicsMotor('XF:06BM-BI{Slt:02-Ax:T}Mtr',  name='dm3_slits_t')
    dm3_slits_b = XAFSEpicsMotor('XF:06BM-BI{Slt:02-Ax:B}Mtr',  name='dm3_slits_b')
    dm3_slits_o.hvel_sp.put(0.2)
    dm3_slits_i.hvel_sp.put(0.2)
    dm3_slits_t.hvel_sp.put(0.2)
    dm3_slits_b.hvel_sp.put(0.2)
    #dm3_slits_i.user_offset.put(-6.0211)
    #dm3_slits_o.user_offset.put(7.9844)
    #dm3_slits_t.user_offset.put(-2.676)
    #dm3_slits_b.user_offset.put(-2.9737)
else:
    dm3_slits_o = SynAxis(name='dm3_slits_o')
    dm3_slits_i = SynAxis(name='dm3_slits_i')
    dm3_slits_t = SynAxis(name='dm3_slits_t')
    dm3_slits_b = SynAxis(name='dm3_slits_b')
    
slits3list = [dm3_slits_o, dm3_slits_i, dm3_slits_t, dm3_slits_b]
mcs8_motors.extend(slits3list)
examine_fmbo_motor_group(slits3list)




## DM2
print(f'{TAB}FMBO motor group: slits2')

slits2 = StandardSlits('XF:06BMA-OP{Slt:01-Ax:',  name='slits2')
slits2.nominal = [18.0, 1.1, 0.0, 0.6]
slits2.top.user_offset.put(-1.4247)
slits2.bottom.user_offset.put(-1.0926)
slits2.bottom.hlm.put(1.5)

wait_for_connection(slits2)

if slits2.connected is True:
    dm2_slits_o = XAFSEpicsMotor('XF:06BMA-OP{Slt:01-Ax:O}Mtr',  name='dm2_slits_o')
    dm2_slits_i = XAFSEpicsMotor('XF:06BMA-OP{Slt:01-Ax:I}Mtr',  name='dm2_slits_i')
    dm2_slits_t = XAFSEpicsMotor('XF:06BMA-OP{Slt:01-Ax:T}Mtr',  name='dm2_slits_o')
    dm2_slits_b = XAFSEpicsMotor('XF:06BMA-OP{Slt:01-Ax:B}Mtr',  name='dm2_slits_b')
    dm2_slits_o.hvel_sp.put(0.2)
    dm2_slits_i.hvel_sp.put(0.2)
    dm2_slits_t.hvel_sp.put(0.2)
    dm2_slits_b.hvel_sp.put(0.2)
else:
    dm2_slits_o = SynAxis(name='dm2_slits_o')
    dm2_slits_i = SynAxis(name='dm2_slits_i')
    dm2_slits_t = SynAxis(name='dm2_slits_t')
    dm2_slits_b = SynAxis(name='dm2_slits_b')
    
    
dm2list = [dm2_slits_o, dm2_slits_i, dm2_slits_t, dm2_slits_b]
mcs8_motors.extend(dm2list)
examine_fmbo_motor_group(dm2list)









#####################################
#  _    _ _   _  _____ _____ _      #
# | |  | | | | ||  ___|  ___| |     #
# | |  | | |_| || |__ | |__ | |     #
# | |/\| |  _  ||  __||  __|| |     #
# \  /\  / | | || |___| |___| |____ #
#  \/  \/\_| |_/\____/\____/\_____/ #
#####################################
                                 
                                 
run_report('\tsample wheels')
from BMM.wheel import WheelMotor, WheelMacroBuilder, reference, show_reference_wheel
from BMM.user_ns.motors import xafs_x, xafs_y, xafs_refx, xafs_refy

xafs_wheel = xafs_rotb  = WheelMotor('XF:06BMA-BI{XAFS-Ax:RotB}Mtr',  name='xafs_wheel')
xafs_wheel.slotone = 0 # -30        # the angular position of slot #1,  this changed Jan 2026
#xafs_wheel.user_offset.put(-0.7821145500000031)
slot = xafs_wheel.set_slot
xafs_wheel.x_motor = xafs_x
xafs_wheel.y_motor = xafs_y
if rkvs.get('BMM:wheel:outer') is None:
    xafs_wheel.outer_position = 0
else:
    xafs_wheel.outer_position   = float(rkvs.get('BMM:wheel:outer'))
xafs_wheel.inner_position   = xafs_wheel.outer_position + 26.0


xafs_ref = WheelMotor('XF:06BMA-BI{XAFS-Ax:Ref}Mtr',  name='xafs_ref')
xafs_ref.slotone = 0        # the angular position of slot #1
xafs_ref.x_motor = xafs_refx
xafs_ref.y_motor = xafs_refy


#                          ring, slot, elem, material, on wheel (ring: 0=outer, 1=inner)
xafs_ref.mapping = {'empty0': [0,  1, 'empty0', 'empty', True],
                    'Ti':     [0,  2, 'Ti', 'Ti foil', True],
                    'V' :     [0,  3, 'V',  'V foil',  True],
                    'Cr':     [0,  4, 'Cr', 'Cr foil', True],
                    'Mn':     [0,  5, 'Mn', 'Mn metal powder', True],
                    'Fe':     [0,  6, 'Fe', 'Fe foil', True],
                    'Co':     [0,  7, 'Co', 'Co foil', True],
                    'Ni':     [0,  8, 'Ni', 'Ni foil', True],
                    'Cu':     [0,  9, 'Cu', 'Cu foil', True],
                    'Zn':     [0, 10, 'Zn', 'Zn foil', True],
                    'Ga':     [0, 11, 'Ga', 'Ga2O3',   True],
                    'Ge':     [0, 12, 'Ge', 'GeO2',    True],
                    'As':     [0, 13, 'As', 'As2O3',   True],
                    'Se':     [0, 14, 'Se', 'Se metal powder', True],
                    'Br':     [0, 15, 'Br', 'bromophenol blue', True],
                    'Zr':     [0, 16, 'Zr', 'Zr foil', True],
                    'Nb':     [0, 17, 'Nb', 'Nb foil', True],
                    'Mo':     [0, 18, 'Mo', 'Mo foil', True],
                    'Pt':     [0, 19, 'Pt', 'Pt foil', True],
                    'Au':     [0, 20, 'Au', 'Au foil', True],
                    'Pb':     [0, 21, 'Pb', 'Pb foil', True],
                    'Bi':     [0, 22, 'Bi', 'BiO2',    True],
                    'Sr':     [0, 23, 'Sr', 'SrTiO3',  True],
                    'Y' :     [0, 24, 'Y',  'Y foil',  True],
                    'Cs':     [1,  1, 'Cs', 'CsNO3',   True],
                    'La':     [1,  2, 'La', 'La(OH)3', True],
                    'Ce':     [1,  3, 'Ce', 'CeO2',    True],
                    'Pr':     [1,  4, 'Pr', 'Pr6O11',  True],
                    'Nd':     [1,  5, 'Nd', 'Nd2O3',   True],
                    'Sm':     [1,  6, 'Sm', 'Sm2O3',   True],
                    'Eu':     [1,  7, 'Eu', 'Eu2O3',   True],
                    'Gd':     [1,  8, 'Gd', 'Gd2O3',   True],
                    'Tb':     [1,  9, 'Tb', 'Tb4O9',   True],
                    'Dy':     [1, 10, 'Dy', 'Dy2O3',   True],
                    'Ho':     [1, 11, 'Ho', 'Ho2O3',   True],
                    'Er':     [1, 12, 'Er', 'Er2O3',   True],
                    'Tm':     [1, 13, 'Tm', 'Tm2O3',   True],
                    'Yb':     [1, 14, 'Yb', 'Yb2O3',   True],
                    'Lu':     [1, 15, 'Lu', 'Lu2O3',   True],
                    'Rb':     [1, 16, 'Rb', 'RbCO3',   True],
                    'Ba':     [1, 17, 'Ba', 'None',    True],     # missing standard
                    'Hf':     [1, 18, 'Hf', 'HfO2',    True],
                    'Ta':     [1, 19, 'Ta', 'Ta2O5',   True],
                    'W' :     [1, 20, 'W',  'WO3',     True],
                    'Re':     [1, 21, 'Re', 'ReO2',    True], 
                    'Os':     [1, 22, 'Os', 'None',    True],     # missing standard
                    'Sc' :    [1, 23, 'Sc', 'Sc metal powder', True],
                    'Ru':     [1, 24, 'Ru', 'Ru metal powder', True],

                    ## commonly measured radionuclides 
                    'Th':     [0, 22, 'Bi', 'BiO2',    False],  # use Bi L1 for Th L3
                    'U' :     [0, 24, 'Y',  'Y foil',  False],  # use Y K for U L3
                    'Pu':     [0, 16, 'Zr', 'Zr foil', False],  # use Zr K for Pu L3
                    'Am':     [0, 16, 'Zr', 'Zr foil', False],  # use Nb K for Am L3
                    'Pm':     [0,  5, 'Mn', 'Mn metal powder', True],   # use Mn K for Pm L3

                    'Ca':     [0,  1, '--', 'None', False],     # missing standard, no reserved slot
                    'Ir':     [0,  1, '--', 'None', False],     # missing standard, no reserved slot
                    'Hg':     [0,  1, '--', 'None', False],     # missing standard, no reserved slot
                    'Tl':     [0,  1, '--', 'None', False],     # missing standard, no reserved slot

                    'Rh':     [0,  1, '--', 'None', False],     # 4d elements -- maybe L edges in the future...
                    'Pd':     [0,  1, '--', 'None', False],
                    'Ag':     [0,  1, '--', 'None', False],
                    'Dd':     [0,  1, '--', 'None', False],
                    'In':     [0,  1, '--', 'None', False],
                    'Sn':     [0,  1, '--', 'None', False],
                    'Sb':     [0,  1, '--', 'None', False],
                    'Te':     [0,  1, '--', 'None', False],
                    'I' :     [0,  1, '--', 'None', False],
                    
                    ## see BMM_configuration.ini for dealing with user-supplied
                    ## standard, e.g. for radiological materials
}
## missing: Tl, Hg, Ca, Sc, Th, U, Pu

if WITH_RADIOLOGICAL:
    try:
        uranium = profile_configuration.get('experiments', 'u_ref').split()
        uranium[0] = int(uranium[0])
        uranium[1] = int(uranium[1])
        uranium[4] = bool(uranium[3])
        xafs_ref.mapping['U'] = uranium
        whisper('Set U standard location')
    except Exception as E:
        #print(E)
        error_msg('Unable to read U reference configuration from INI file')
        pass
    try:
        technicium = profile_configuration.get('experiments', 'tc_ref').split()
        technicium[0] = int(technicium[0])
        technicium[1] = int(technicium[1])
        technicium[4] = bool(technicium[3])
        xafs_ref.mapping['Tc'] = technicium
        whisper('Set Tc standard location')
    except Exception as E:
        #print(E)
        error_msg('Unable to read Tc reference configuration from INI file')
        pass
    try:
        thorium = profile_configuration.get('experiments', 'th_ref').split()
        thorium[0] = int(thorium[0])
        thorium[1] = int(thorium[1])
        thorium[4] = bool(thorium[3])
        xafs_ref.mapping['Th'] = thorium
        whisper('Set Th standard location')
    except Exception as E:
        #print(E)
        error_msg('Unable to read Th reference configuration from INI file')
        pass




def set_reference_wheel(position=None):
    '''Run this after measuring the correct location of xafs_refx.  This
    will set the inner and outer positions in bsui, push the outer
    position to redis, and set sensible limits on xafs_refx.  If no
    position is supplied, the current position of xafs_refx will be
    used.

    '''
    if position is None:
        position = xafs_refx.position
    xafs_ref.outer_position = position
    xafs_ref.inner_position = position + 26.5
    rkvs.set('BMM:ref:outer', position)
    xafs_refx.llm.put(xafs_ref.outer_position - 11.5)
    xafs_refx.hlm.put(xafs_ref.inner_position + 11.5)

if rkvs.get('BMM:ref:outer') is None:
    xafs_ref.outer_position = 0.0
    error_msg('\t\t\t\tReference wheel is not aligned!')
elif profile_configuration.getboolean('experiments', 'use_reference') is True:    
    set_reference_wheel(float(rkvs.get('BMM:ref:outer')))
#    xafs_ref.outer_position   = float(rkvs.get('BMM:ref:outer'))
#xafs_ref.inner_position = xafs_ref.outer_position + 26.5 # xafs_ref.outer_position + ~26.5

    
def ref2redis():
    #for i in range(0, rkvs.llen('BMM:reference:list')):
    #    rkvs.rpop('BMM:reference:list')
    rkvs.set('BMM:reference:mapping', json.dumps(xafs_ref.mapping))

ref2redis()

def setup_wheel():
    yield from mv(xafs_x, -119.7, xafs_y, 112.1, xafs_wheel, 0)
    

wmb = WheelMacroBuilder()
wmb.description = 'a standard sample wheel'
wmb.instrument  = 'sample wheel'
wmb.folder      = BMMuser.workspace
wmb.cleanup     = 'yield from xafs_wheel.reset()' 



######################################################################################
# ______ _____ _____ _____ _____ _____ ___________  ___  ________ _   _ _   _ _____  #
# |  _  \  ___|_   _|  ___/  __ \_   _|  _  | ___ \ |  \/  |  _  | | | | \ | |_   _| #
# | | | | |__   | | | |__ | /  \/ | | | | | | |_/ / | .  . | | | | | | |  \| | | |   #
# | | | |  __|  | | |  __|| |     | | | | | |    /  | |\/| | | | | | | | . ` | | |   #
# | |/ /| |___  | | | |___| \__/\ | | \ \_/ / |\ \  | |  | \ \_/ / |_| | |\  | | |   #
# |___/ \____/  \_/ \____/ \____/ \_/  \___/\_| \_| \_|  |_/\___/ \___/\_| \_/ \_/   #
######################################################################################

run_report('\tdetector mount')
from BMM.detector_mount import DetectorMount  #, find_detector_position
det = DetectorMount()




###########################################################
#   ___  _____ _____ _   _  ___ _____ ___________  _____  #
#  / _ \/  __ \_   _| | | |/ _ \_   _|  _  | ___ \/  ___| #
# / /_\ \ /  \/ | | | | | / /_\ \| | | | | | |_/ /\ `--.  #
# |  _  | |     | | | | | |  _  || | | | | |    /  `--. \ #
# | | | | \__/\ | | | |_| | | | || | \ \_/ / |\ \ /\__/ / #
# \_| |_/\____/ \_/  \___/\_| |_/\_/  \___/\_| \_|\____/  #
###########################################################

run_report('\tactuators')
from bmm_tools.devices.actuators import BMPS_Shutter, IDPS_Shutter, EPS_Shutter

try:
    bmps = BMPS_Shutter('SR:C06-EPS{PLC:1}', name='BMPS')
except:
    pass

try:
    idps = IDPS_Shutter('SR:C06-EPS{PLC:1}', name = 'IDPS')
except:
    pass


sha = EPS_Shutter('XF:06BM-PPS{Sh:FE}', name = 'Front-End Shutter')
sha.shutter_type = 'FE'
sha.openval  = 0
sha.closeval = 1
shb = EPS_Shutter('XF:06BM-PPS{Sh:A}', name = 'Photon Shutter')
shb.shutter_type = 'PH'
shb.openval  = 0
shb.closeval = 1

# Plan names to open and close the shutters from RE Worker (need distinct name)
shb_open_plan = shb.open_plan
shb_close_plan = shb.close_plan




fs1 = EPS_Shutter('XF:06BMA-OP{FS:1}', name = 'FS1')
fs1.shutter_type = 'FS'
fs1.openval  = 1
fs1.closeval = 0


ln2 = EPS_Shutter('XF:06BM-PU{LN2-Main:IV}', name = 'LN2')
ln2.shutter_type = 'LN'
ln2.openval  = 1
ln2.closeval = 0



###############################################
# ______ _____ _    _____ ___________  _____  #
# |  ___|_   _| |  |_   _|  ___| ___ \/  ___| #
# | |_    | | | |    | | | |__ | |_/ /\ `--.  #
# |  _|   | | | |    | | |  __||    /  `--. \ #
# | |    _| |_| |____| | | |___| |\ \ /\__/ / #
# \_|    \___/\_____/\_/ \____/\_| \_|\____/  #
###############################################


# run_report('\tfilters')
# from BMM.attenuators import attenuator, filter_state, set_filters
# from BMM.user_ns.motors import dm1_filters1, dm1_filters2
# filter1 = attenuator()
# filter1.motor = dm1_filters1
# filter2 = attenuator()
# filter2.motor = dm1_filters2



###############################################################################
# ____________ _____ _   _ _____      _____ _   _______  _______________  ___ #
# |  ___| ___ \  _  | \ | |_   _|    |  ___| \ | |  _  \ | ___ \ ___ \  \/  | #
# | |_  | |_/ / | | |  \| | | |______| |__ |  \| | | | | | |_/ / |_/ / .  . | #
# |  _| |    /| | | | . ` | | |______|  __|| . ` | | | | | ___ \  __/| |\/| | #
# | |   | |\ \\ \_/ / |\  | | |      | |___| |\  | |/ /  | |_/ / |   | |  | | #
# \_|   \_| \_|\___/\_| \_/ \_/      \____/\_| \_/___/   \____/\_|   \_|  |_/ #
###############################################################################

                                                                           
run_report('\tfront-end beam position monitor')
from bmm_tools.devices.frontend import FEBPM

try:
    bpm_upstream   = FEBPM('SR:C06-BI{BPM:4}Pos:', name='bpm_upstream')
    bpm_downstream = FEBPM('SR:C06-BI{BPM:5}Pos:', name='bpm_downstream')
except:
    pass

def read_bpms():
    return(bpm_upstream.x.get(), bpm_upstream.y.get(), bpm_downstream.x.get(), bpm_downstream.y.get())



####################################################################
#  _____ _   _ _______   __ ______ _____ _   _ _____ _____  _____  #
# | ___ \ | | /  ___\ \ / / |  _  \  ___| | | |_   _/  __ \|  ___| #
# | |_/ / | | \ `--. \ V /  | | | | |__ | | | | | | | /  \/| |__   #
# | ___ \ | | |`--. \ \ /   | | | |  __|| | | | | | | |    |  __|  #
# | |_/ / |_| /\__/ / | |   | |/ /| |___\ \_/ /_| |_| \__/\| |___  #
# \____/ \___/\____/  \_/   |___/ \____/ \___/ \___/ \____/\____/  #
####################################################################
                                                                
run_report('\tbusy device')
from bmm_tools.devices.busy import Busy
busy = Busy(name='busy')


#############################################
#  _     _____ _   _  _   __  ___  ___  ___ #
# | |   |_   _| \ | || | / / / _ \ |  \/  | #
# | |     | | |  \| || |/ / / /_\ \| .  . | #
# | |     | | | . ` ||    \ |  _  || |\/| | #
# | |_____| |_| |\  || |\  \| | | || |  | | #
# \_____/\___/\_| \_/\_| \_/\_| |_/\_|  |_/ #
#############################################

linkam, lmb = None, None
if WITH_LINKAM:
    run_report('\tLinkam controller')
    from BMM.linkam import Linkam, LinkamMacroBuilder
    linkam = Linkam('XF:06BM-ES{LINKAM}:', name='linkam', egu='°C', settle_time=10, limits=(-196.1,560.0))

    lmb = LinkamMacroBuilder()
    lmb.description = 'the Linkam stage'
    lmb.instrument='Linkam'
    lmb.folder = BMMuser.workspace



##############################################################
#  _       ___   _   __ _____ _____ _   _ ___________ _____  #
# | |     / _ \ | | / /|  ___/  ___| | | |  _  | ___ \  ___| #
# | |    / /_\ \| |/ / | |__ \ `--.| |_| | | | | |_/ / |__   #
# | |    |  _  ||    \ |  __| `--. \  _  | | | |    /|  __|  #
# | |____| | | || |\  \| |___/\__/ / | | \ \_/ / |\ \| |___  #
# \_____/\_| |_/\_| \_/\____/\____/\_| |_/\___/\_| \_\____/  #
##############################################################

lakeshore, lsmb = None, None
if WITH_LAKESHORE:
    run_report('\tLakeShore 331 controller')
    from BMM.lakeshore import LakeShore, LakeShoreMacroBuilder
    lakeshore = LakeShore('XF:06BM-BI{LS:331-1}:', name='LakeShore 331', egu='K', settle_time=10, limits=(5,400.0))
    ## 1 second updates on scan and ctrl
    lakeshore.temp_scan_rate.put(6)
    lakeshore.ctrl_scan_rate.put(6)
    lakeshore.ramp_rate.put(5)

    lsmb = LakeShoreMacroBuilder()
    lsmb.description = 'the LakeShore 331 temperature controller'
    lsmb.instrument='LakeShore'
    lsmb.folder = BMMuser.workspace
    




###############################################################
# ___  ________ _____ ___________   _____ ______ ___________  #
# |  \/  |  _  |_   _|  _  | ___ \ |  __ \| ___ \_   _|  _  \ #
# | .  . | | | | | | | | | | |_/ / | |  \/| |_/ / | | | | | | #
# | |\/| | | | | | | | | | |    /  | | __ |    /  | | | | | | #
# | |  | \ \_/ / | | \ \_/ / |\ \  | |_\ \| |\ \ _| |_| |/ /  #
# \_|  |_/\___/  \_/  \___/\_| \_|  \____/\_| \_|\___/|___/   #
###############################################################

run_report('\tmotor grid automation')
from BMM.grid import GridMacroBuilder
gmb = GridMacroBuilder()
gmb.description = 'a motor grid'
gmb.instrument = 'grid'
gmb.folder = BMMuser.workspace


###################################################################################################################################
# ______ _____ _____  _____ _   _   ___   _   _ _____  ______ ___________ _      _____ _____ _____ _____ _   _ _____ _______   __ #
# | ___ \  ___/  ___||  _  | \ | | / _ \ | \ | |_   _| | ___ \  ___|  ___| |    |  ___/  __ \_   _|_   _| | | |_   _|_   _\ \ / / #
# | |_/ / |__ \ `--. | | | |  \| |/ /_\ \|  \| | | |   | |_/ / |__ | |_  | |    | |__ | /  \/ | |   | | | | | | | |   | |  \ V /  #
# |    /|  __| `--. \| | | | . ` ||  _  || . ` | | |   |    /|  __||  _| | |    |  __|| |     | |   | | | | | | | |   | |   \ /   #
# | |\ \| |___/\__/ /\ \_/ / |\  || | | || |\  | | |   | |\ \| |___| |   | |____| |___| \__/\ | |  _| |_\ \_/ /_| |_  | |   | |   #
# \_| \_\____/\____/  \___/\_| \_/\_| |_/\_| \_/ \_/   \_| \_\____/\_|   \_____/\____/ \____/ \_/  \___/ \___/ \___/  \_/   \_/   #
###################################################################################################################################

refldet = None
if profile_configuration.getboolean('detectors', 'pilatus') is True:
    refldet = 'pilatus'
if profile_configuration.getboolean('detectors', 'eiger') is True:
    refldet = 'eiger'

refl = None
if refldet is not None:
    run_report('\tresonant reflectivity automation')
    from BMM.reflectivity import ResonantReflectivityMacroBuilder, it_in, it_out
    refl = ResonantReflectivityMacroBuilder(detector=refldet)
    refl.description = 'a resonant reflectivity experiment'
    refl.instrument = 'resonant reflectivity'
    refl.folder = BMMuser.workspace
    refl.use_roi2.put(1)
    refl.use_roi3.put(1)




####################################################################################
#  _   _______ _      _       _____  _    _ _____ _____ _____  _   _  _____ _____  #
# | | / /_   _| |    | |     /  ___|| |  | |_   _|_   _/  __ \| | | ||  ___/  ___| #
# | |/ /  | | | |    | |     \ `--. | |  | | | |   | | | /  \/| |_| || |__ \ `--.  #
# |    \  | | | |    | |      `--. \| |/\| | | |   | | | |    |  _  ||  __| `--. \ #
# | |\  \_| |_| |____| |____ /\__/ /\  /\  /_| |_  | | | \__/\| | | || |___/\__/ / #
# \_| \_/\___/\_____/\_____/ \____/  \/  \/ \___/  \_/  \____/\_| |_/\____/\____/  #
####################################################################################
                                                                                

run_report('\tamplifier kill switches')
from bmm_tools.tools.killswitch import KillSwitch
from BMM.user_ns.dcm import dcm
from BMM.user_ns.motors import dm3_bct, dm3_bpm, dm3_foils, dm3_fs

ks = KillSwitch('XF:06BMB-CT{DIODE-Local:4}', name='amplifier kill switches')
ks.dcm_device = dcm
ks.slits2_device = slits2
ks.slits3_device = slits3
ks.m2_device = m2
ks.m3_device = m3
ks.dm3_axes = (dm3_bct, dm3_bpm, dm3_foils, dm3_fs)




#######################################################
#  _   _ ___________   _   _ ___________ _____ _____  #
# | | | /  ___| ___ \ | | | |_   _|  _  \  ___|  _  | #
# | | | \ `--.| |_/ / | | | | | | | | | | |__ | | | | #
# | | | |`--. \ ___ \ | | | | | | | | | |  __|| | | | #
# | |_| /\__/ / |_/ / \ \_/ /_| |_| |/ /| |___\ \_/ / #
#  \___/\____/\____/   \___/ \___/|___/ \____/ \___/  #
#######################################################
                                                   

# run_report('\tvideo recording via USB cameras')
# from BMM.video import USBVideo
# usbvideo1 = USBVideo('XF:06BM-ES{UVC-Cam:1}CV1:', name='usbvideo1')
# usbvideo1.path = '/nsls2/data/bmm/assets/usbcam/'  # this ain't right
# usbvideo1.initialize()

# usbvideo2 = USBVideo('XF:06BM-ES{UVC-Cam:1}CV2:', name='usbvideo2')
# usbvideo2.enable.put(0)
# usbvideo2.visionfunction3.put(4)
# usbvideo2.path.put('/nsls2/data/bmm/assets/usbcam/')  # this ain't right
# usbvideo2.framerate.put(60)
# usbvideo2.startstop.put(0)
