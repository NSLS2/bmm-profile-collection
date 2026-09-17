
from BMM.functions import run_report
from BMM.metadata import TC

from bmm_tools.devices.ring import Ring

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

run_report(__file__, text='miscellaneous metadata and the baseline')

first_crystal  = TC('XF:06BMA-OP{Mono:DCM-Crys:1}',      name='first_crystal')
compton_shield = TC('XF:06BMA-OP{Mono:DCM-Crys:1-Ax:R}', name='compton_shield')

ring = Ring('SR', name='ring')

from BMM.user_ns.dcm import dcm
from BMM.user_ns.motors import *
from BMM.user_ns.instruments import * #xafs_wheel, xafs_ref, slits2, slits3, m2, m3, xafs_table, m2_bender

user_ns['sd'].baseline = [xafs_x, xafs_y, xafs_pitch, xafs_roll, xafs_wheel, xafs_rots, xafs_garot,
                          xafs_detx, xafs_dety, xafs_detz,
                          xafs_ref, xafs_refx, xafs_refy,
                          xafs_bsx, xafs_bsy, xafs_adx, xafs_ady, xafs_spare,
                          dm3_bct, dm3_foils, dm2_fs,
                          dcm.x, dcm.pitch, dcm.roll, # dcm.bragg, dcm.para, dcm.pitch,
                          slits3.top, slits3.bottom, slits3.outboard, slits3.inboard, slits3.vsize, slits3.vcenter, slits3.hsize, slits3.hcenter, 
                          slits2.top, slits2.bottom, slits2.outboard, slits2.inboard, slits2.vsize, slits2.vcenter, slits2.hsize, slits2.hcenter,
                          #m1.yu, m1.ydo, m1.ydi, m1.xu, m1.xd, m1.vertical, m1.lateral, m1.pitch, m1.roll, m1.yaw,
                          m2.yu, m2.ydo, m2.ydi, m2.xu, m2.xd, m2.vertical, m2.lateral, m2.pitch, m2.roll, m2.yaw, m2_bender,
                          m3.yu, m3.ydo, m3.ydi, m3.xu, m3.xd, m3.vertical, m3.lateral, m3.pitch, m3.roll, m3.yaw,
                          xafs_table.yu, xafs_table.yd, # xafs_table.ydi, # xafs_xu , xafs_xd,
                          xafs_table.vertical, xafs_table.pitch, # xafs_table.roll, 
]

#sd.baseline = ()
