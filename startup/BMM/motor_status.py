
from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from bmm_tools.tools.messages import boxedtext

def motor_metadata(uid=None):
    biglist = (user_ns['xafs_linx'], user_ns['xafs_liny'], user_ns['xafs_pitch'], user_ns['xafs_roll'],
               user_ns['xafs_linxs'], user_ns['xafs_wheel'], user_ns['xafs_rots'], user_ns['xafs_ref'],
               #user_ns['xafs_lins'],
               user_ns['xafs_mtr8'], user_ns['xafs_refx'],
               
               user_ns['dm3_bct'], user_ns['dm3_foils'], user_ns['dm2_fs'],
               user_ns['slits3'].top, user_ns['slits3'].bottom, user_ns['slits3'].outboard, user_ns['slits3'].inboard,
               user_ns['slits3'].vsize, user_ns['slits3'].vcenter, user_ns['slits3'].hsize, user_ns['slits3'].hcenter,
               
               user_ns['slits2'].top, user_ns['slits2'].bottom, user_ns['slits2'].outboard, user_ns['slits2'].inboard,
               user_ns['slits2'].vsize, user_ns['slits2'].vcenter, user_ns['slits2'].hsize, user_ns['slits2'].hcenter,
               
               user_ns['m1'].yu, user_ns['m1'].ydo, user_ns['m1'].ydi, user_ns['m1'].xu, user_ns['m1'].xd,
               user_ns['m1'].vertical, user_ns['m1'].lateral, user_ns['m1'].pitch, user_ns['m1'].roll, user_ns['m1'].yaw,
               
               user_ns['m2'].yu, user_ns['m2'].ydo, user_ns['m2'].ydi, user_ns['m2'].xu, user_ns['m2'].xd,
               user_ns['m2'].vertical, user_ns['m2'].lateral, user_ns['m2'].pitch, user_ns['m2'].roll, user_ns['m2'].yaw, user_ns['m2_bender'],
               
               user_ns['m3'].yu, user_ns['m3'].ydo, user_ns['m3'].ydi, user_ns['m3'].xu, user_ns['m3'].xd,
               user_ns['m3'].vertical, user_ns['m3'].lateral, user_ns['m3'].pitch, user_ns['m3'].roll, user_ns['m3'].yaw,
               
               user_ns['xafs_table'].yu, user_ns['xafs_table'].ydo, user_ns['xafs_table'].ydi, #user_ns['xafs_xu'], user_ns['xafs_xd'],
               user_ns['xafs_table'].vertical, user_ns['xafs_table'].pitch, user_ns['xafs_table'].roll, 
           )
    md = dict()
    table = None
    try:
        table = bmm_catalog[uid].baseline.read()
    except:
        pass
    for m in biglist:
        if table is None:
            try:
                md[m.name] = m.position
            except:
                pass
        else:
            md[m.name] = float(table[m.name][1])
    return(md)

def motor_status():
    md = motor_metadata()

    text = '\n Energy = %.1f eV   reflection = Si(%s)   mode = %s\n' % (user_ns['dcm'].energy.readback.get(), user_ns['dcm']._crystal, user_ns['dcm'].mode)
    text += '      Bragg = %8.5f   2nd Xtal Perp  = %7.4f   Para = %8.4f\n' % \
            (user_ns['dcm'].bragg.user_readback.get(), user_ns['dcm'].perp.user_readback.get(), user_ns['dcm'].para.user_readback.get())
    text += '                                  Pitch = %7.4f   Roll = %8.4f\n\n' % \
            (user_ns['dcm'].pitch.user_readback.get(), user_ns['dcm'].roll.user_readback.get())

    text += ' M2\n      vertical = %7.3f mm            YU  = %7.3f mm\n' % (md[user_ns['m2'].vertical.name], md[user_ns['m2'].yu.name])
    text += '      lateral  = %7.3f mm            YDO = %7.3f mm\n'      % (md[user_ns['m2'].lateral.name],  md[user_ns['m2'].ydo.name])
    text += '      pitch    = %7.3f mrad          YDI = %7.3f mm\n'      % (md[user_ns['m2'].pitch.name],    md[user_ns['m2'].ydi.name])
    text += '      roll     = %7.3f mrad          XU  = %7.3f mm\n'      % (md[user_ns['m2'].roll.name],     md[user_ns['m2'].xu.name])
    text += '      yaw      = %7.3f mrad          XD  = %7.3f mm\n'      % (md[user_ns['m2'].yaw.name],      md[user_ns['m2'].xd.name])
    text += '      bender   = %9.1f steps\n\n'                           %  md[user_ns['m2_bender'].name]

    stripe = '(Rh/Pt stripe)'
    if user_ns['m3'].xu.user_readback.get() < 0:
        stripe = '(Si stripe)'

    text += ' M3  %s\n'                                                 % stripe
    text += '      vertical = %7.3f mm            YU  = %7.3f mm\n'     % (md[user_ns['m3'].vertical.name], md[user_ns['m3'].yu.name])
    text += '      lateral  = %7.3f mm            YDO = %7.3f mm\n'     % (md[user_ns['m3'].lateral.name],  md[user_ns['m3'].ydo.name])
    text += '      pitch    = %7.3f mrad          YDI = %7.3f mm\n'     % (md[user_ns['m3'].pitch.name],    md[user_ns['m3'].ydi.name])
    text += '      roll     = %7.3f mrad          XU  = %7.3f mm\n'     % (md[user_ns['m3'].roll.name],     md[user_ns['m3'].xu.name])
    text += '      yaw      = %7.3f mrad          XD  = %7.3f mm\n\n'   % (md[user_ns['m3'].yaw.name],      md[user_ns['m3'].xd.name])

    text += ' Slits2:  vsize  vcenter  hsize   hcenter     top    bottom    outboard  inboard\n'
    text += '        %7.3f %7.3f %7.3f %7.3f    %7.3f  %7.3f  %7.3f  %7.3f\n\n' % \
            (md[user_ns['slits2'].vsize.name], md[user_ns['slits2'].vcenter.name],
             md[user_ns['slits2'].hsize.name], md[user_ns['slits2'].hcenter.name],
             md[user_ns['slits2'].top.name], md[user_ns['slits2'].bottom.name],
             md[user_ns['slits2'].outboard.name], md[user_ns['slits2'].inboard.name])

    text += ' Slits3:  vsize  vcenter  hsize   hcenter     top    bottom    outboard  inboard\n'
    text += '        %7.3f %7.3f %7.3f %7.3f    %7.3f  %7.3f  %7.3f  %7.3f\n\n' % \
            (md[user_ns['slits3'].vsize.name], md[user_ns['slits3'].vcenter.name],
             md[user_ns['slits3'].hsize.name], md[user_ns['slits3'].hcenter.name],
             md[user_ns['slits3'].top.name], md[user_ns['slits3'].bottom.name],
             md[user_ns['slits3'].outboard.name], md[user_ns['slits3'].inboard.name])

    text += ' DM3_BCT: %7.3f mm      '   % md[user_ns['dm3_bct'].name]
    text += ' DM3_foils: %7.3f mm      ' % md[user_ns['dm3_foils'].name]
    text += ' DM2_fs: %7.3f mm\n\n'      % md[user_ns['dm2_fs'].name]

    text += ' XAFS table:\n      vertical  pitch    roll   YU     YDO     YDI\n'
    text += '       %7.3f %7.3f %7.3f %7.3f %7.3f %7.3f\n\n' % \
            (md[user_ns['xafs_table'].vertical.name], md[user_ns['xafs_table'].pitch.name], md[user_ns['xafs_table'].roll.name],
             md[user_ns['xafs_table'].yu.name], md[user_ns['xafs_table'].ydo.name], md[user_ns['xafs_table'].ydi.name])

    text += ' XAFS stages (motor names are xafs_<name>, units mm or deg):\n'
    text += '     name =     x        y     pitch    wheel (slot)   ref\n'
    text += '           %8.3f %8.3f %7.3f %8.3f   %d   %8.3f\n' % \
            (md[user_ns['xafs_linx'].name],
             md[user_ns['xafs_liny'].name],
             md[user_ns['xafs_pitch'].name],
             md[user_ns['xafs_rotb'].name], user_ns['xafs_rotb'].current_slot(user_ns['xafs_rotb'].position),
             md[user_ns['xafs_ref'].name]
            )

    return '[white]' + text + '[/white]'

def ms():
    if user_ns['BMMuser'].syns is True:
        print('Some motors are disconnected and represented as a SynAxis.')
        print('Do check_for_synaxis() for more information.')
        return 
    boxedtext(motor_status(), title='BMM motor status', color='green')





def xrd_motors():
    text = '\n'
    for m in (xrd_delta,  xrd_eta,    xrd_chi,    xrd_phi,    xrd_mu,     xrd_nu,
              xrd_anal,   xrd_det,    xrd_dethor, xrd_wheel1, xrd_wheel2,
              xrd_samx,   xrd_samy,   xrd_samz,   xrd_tabyd,  xrd_tabyui,
              xrd_tabyuo, xrd_tabxu,  xrd_tabxd,  xrd_tabz,   xrd_slit1t,
              xrd_slit1b, xrd_slit1i, xrd_slit1o):
        text += '  %-26s: %8.3f %s\n' % (m.name, m.user_readback.get(), m.describe()[m.name]['units'])
    return text

def xrdm():
    boxedtext(xrd_motors(), title='XRD motor status', color='green')
