from BMM.functions import run_report
run_report(__file__, text='Monochromator definitions')

from bmm_tools.tools.wait_for_connection import wait_for_connection
from bmm_tools.devices.motors import FMBOEpicsMotor, VacuumEpicsMotor, XAFSEpicsMotor, DeadbandEpicsMotor, BMMDeadBandMotor

from BMM.user_ns.motors import mcs8_motors
from BMM.functions      import examine_fmbo_motor_group
from BMM.user_ns.base   import startup_dir, profile_configuration
from BMM.user_ns.bmm    import BMMuser

# see comment at top of BMM/user_ns/instruments.py
from ophyd.sim import SynAxis


TAB = '\t\t\t'

dcm = False
#from BMM.dcm import DCM
from bmm_tools.devices.dcm import DCM
#from BMM.user_ns.motors import dcm_x

dcm = DCM('XF:06BMA-OP{Mono:DCM1-Ax:', name='dcm', crystal='111')
dcm.roll_111 = profile_configuration['dcm']['roll_111']
dcm.acc_fast = BMMuser.acc_fast
wait_for_connection(dcm)

print(f'{TAB}FMBO motor group: dcm')
if dcm.connected is True:
    # dcm_bragg = XAFSEpicsMotor('XF:06BMA-OP{Mono:DCM1-Ax:Bragg}Mtr', name='dcm_bragg')
    # #dcm_bragg = BMMDeadBandMotor('XF:06BMA-OP{Mono:DCM1-Ax:Bragg}Mtr', name='dcm_bragg')
    # dcm_pitch = VacuumEpicsMotor('XF:06BMA-OP{Mono:DCM1-Ax:P2}Mtr',    name='dcm_pitch')
    # dcm_roll  = VacuumEpicsMotor('XF:06BMA-OP{Mono:DCM1-Ax:R2}Mtr',    name='dcm_roll')
    # dcm_perp  = XAFSEpicsMotor('XF:06BMA-OP{Mono:DCM1-Ax:Per2}Mtr',  name='dcm_perp')
    # dcm_para  = XAFSEpicsMotor('XF:06BMA-OP{Mono:DCM1-Ax:Par2}Mtr',  name='dcm_para')
    # dcm_x     = XAFSEpicsMotor('XF:06BMA-OP{Mono:DCM1-Ax:X}Mtr',     name='dcm_x')
    # dcm_y     = XAFSEpicsMotor('XF:06BMA-OP{Mono:DCM1-Ax:Y}Mtr',     name='dcm_y')

    dcm.para.hlm.put(161)        # this is 21200 on the Si(111) mono
    #                            # hard limit is at 162.48

    if hasattr(dcm.bragg, 'tolerance'):  # relevant to Jamie's DeadbandEpicsMotor
        dcm.bragg.tolerance.put(0.0004)
    dcm.bragg.encoder.kind = 'hinted'
    dcm.bragg.user_readback.kind = 'hinted'
    dcm.bragg.user_setpoint.kind = 'config'
    dcm.bragg.velocity.put(0.4)
    dcm.bragg.acceleration.put(BMMuser.acc_fast)

    ## for some reason, this needs to be set explicitly
    dcm.x.hlm.put(68)
    dcm.x.llm.put(0)
    dcm.x.velocity.put(0.6)
    dcm.x._limits = (0, 68)

    ## dcm.para: 1.25 results in a following error
    dcm.para.velocity.put(0.6)  # sped up 06/26/23 to attempt to deal with stalling at positions > 125
    dcm.para.hvel_sp.put(0.4)
    dcm.perp.velocity.put(0.2)
    dcm.perp.hvel_sp.put(0.2)
    #dcm.para.llm.put(12.3)
    #dcm.para.hlm.put(158.5)
    dcm.perp.llm.put(1.39)
    dcm.perp.hlm.put(26.5)


    if dcm.x.user_readback.get() > 10:
        dcm.set_crystal('311')
    else:
        dcm.set_crystal('111')
    
# else:
#     dcm.bragg = SynAxis(name='dcm_bragg')
#     dcm.pitch = SynAxis(name='dcm_pitch')
#     dcm.roll  = SynAxis(name='dcm_roll')
#     dcm.perp  = SynAxis(name='dcm_perp')
#     dcm.para  = SynAxis(name='dcm_para')
#     dcm.x     = SynAxis(name='dcm_x')
#     dcm.y     = SynAxis(name='dcm_y')
    
dcmlist = [dcm.bragg, dcm.pitch, dcm.roll, dcm.perp, dcm.para, dcm.x, dcm._y]
mcs8_motors.extend(dcmlist)
examine_fmbo_motor_group(dcmlist)

