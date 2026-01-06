
from bmm_tools.devices.slits import GonioSlits

## goniometer table
# print(f'{TAB}XRD motor group')
# gonio_table = GonioTable('XF:06BM-ES{SixC-Ax:Tbl_', name='gonio_table', mirror_length=1117.6,  mirror_width=711.12)
# wait_for_connection(gonio_table)

# xrd_delta  = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:VTTH}Mtr',    name='delta')
# xrd_eta    = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:VTH}Mtr',     name='eta')
# xrd_chi    = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:CHI}Mtr',     name='chi')
# xrd_phi    = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:PHI}Mtr',     name='phi')
# xrd_mu     = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:HTH}Mtr',     name='mu')
# xrd_nu     = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:HTTH}Mtr',    name='nu')

# xrd_anal   = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:ANAL}Mtr',    name='analyzer')
# xrd_det    = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:DET}Mtr',     name='detector')
# xrd_dethor = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:DETHOR}Mtr',  name='detector horizontal')

# xrd_wheel1 = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:WHEEL1}Mtr',  name='wheel1')
# xrd_wheel2 = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:WHEEL2}Mtr',  name='wheel2')

# xrd_samx   = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:SAMX}Mtr',    name='sample x')
# xrd_samy   = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:SAMY}Mtr',    name='sample y')
# xrd_samz   = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:SAMZ}Mtr',    name='sample z')

# xrd_tabyd  = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:Tbl_YD}Mtr',  name='table y downstream')
# xrd_tabyui = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:Tbl_YUI}Mtr', name='table y upstream inboard')
# xrd_tabyuo = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:Tbl_YUO}Mtr', name='table y upstream outboard')
# xrd_tabxu  = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:Tbl_XU}Mtr',  name='table x upstream')
# xrd_tabxd  = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:Tbl_XD}Mtr',  name='table x downstream')
# xrd_tabz   = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:Tbl_Z}Mtr',   name='table z')

# xrd_slit1t = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:Slt1_T}Mtr',  name='slit 1 top')
# xrd_slit1b = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:Slt1_B}Mtr',  name='slit 1 bottom')
# xrd_slit1i = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:Slt1_I}Mtr',  name='slit 1 inboard')
# xrd_slit1o = EndStationEpicsMotor('XF:06BM-ES{SixC-Ax:Slt1_O}Mtr',  name='slit 1 outboard')


slitsg = GonioSlits('XF:06BM-ES{SixC-Ax:Slt1_',  name='slitsg')
