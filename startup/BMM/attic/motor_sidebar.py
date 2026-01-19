def motor_sidebar(md=None):
    '''Generate a list of motor positions to be used in the static html page for a scan sequence.
    Return value is a long string with html tags and entities embedded in the string.

    Parameters
    ----------
    md : dict
        dict with motor positions keyed by <motor>.name

    If md is not provided, the current motor positions will be used.
    If taking a record from Data Broker, the motor positions have been
    recorded in the baseline.  If generating a dossier from a Data
    Broker record, do:

    >>> text = motor_sidebar(uid=uid)

    where uid is the ID of the scan.
    '''
    if type(md) == str:
        md = motor_metadata(md)
    if md is None or type(md) is not dict:
        md = motor_metadata()
    motors = ''

    motors +=  '<span class="motorheading">XAFS stages:</span>\n'
    motors +=  '            <div id="motorgrid">\n'
    motors += f'              <div>xafs_x, {md[user_ns["xafs_x"].name]:.3f}</div>\n'
    motors += f'              <div>xafs_y, {md[user_ns["xafs_y"].name]:.3f}</div>\n'
    motors += f'              <div>xafs_pitch, {md[user_ns["xafs_pitch"].name]:.3f}</div>\n'
    motors += f'              <div>xafs_roll, {md[user_ns["xafs_roll"].name]:.3f}</div>\n'
    motors += f'              <div>xafs_wheel, {md[user_ns["xafs_wheel"].name]:.3f}</div>\n'
    motors += f'              <div>xafs_ref, {md[user_ns["xafs_ref"].name]:.3f}</div>\n'
    motors += f'              <div>xafs_refx, {md[user_ns["xafs_refx"].name]:.3f}</div>\n'
    motors += f'              <div>xafs_refy, {md[user_ns["xafs_refy"].name]:.3f}</div>\n'
    motors += f'              <div>xafs_det, {md[user_ns["xafs_det"].name]:.3f}</div>\n'
    motors += f'              <div>xafs_garot, {md[user_ns["xafs_garot"].name]:.3f}</div>\n'
    motors +=  '            </div>\n'

    motors +=  '            <span class="motorheading">Instruments:</span>\n'
    motors +=  '            <div id="motorgrid">\n'
    motors += f'              <div>slot, {user_ns["xafs_wheel"].current_slot():.3f}</div>\n'
    motors += f'              <div>spinner, {user_ns["ga"].current():.3f}</div>\n'
    motors += f'              <div>dm3_bct, {md[user_ns["dm3_bct"].name]:.3f}</div>\n'
    motors +=  '            </div>\n'

    motors +=  '            <span class="motorheading">Slits3:</span>\n'
    motors +=  '            <div id="motorgrid">\n'
    motors += f'              <div>slits3_vsize, {md[user_ns["slits3"].vsize.name]:.3f}</div>\n'
    motors += f'              <div>slits3_vcenter, {md[user_ns["slits3"].vcenter.name]:.3f}</div>\n'
    motors += f'              <div>slits3_hsize, {md[user_ns["slits3"].hsize.name]:.3f}</div>\n'
    motors += f'              <div>slits3_hcenter, {md[user_ns["slits3"].hcenter.name]:.3f}</div>\n'
    motors += f'              <div>slits3_top, {md[user_ns["slits3"].top.name]:.3f}</div>\n'
    motors += f'              <div>slits3_bottom, {md[user_ns["slits3"].bottom.name]:.3f}</div>\n'
    motors += f'              <div>slits3_inboard, {md[user_ns["slits3"].inboard.name]:.3f}</div>\n'
    motors += f'              <div>slits3_outboard, {md[user_ns["slits3"].outboard.name]:.3f}</div>\n'
    motors +=  '            </div>\n'

    motors +=  '            <span class="motorheading">M2:</span>\n'
    motors +=  '            <div id="motorgrid">\n'
    motors += f'              <div>m2_vertical, {md[user_ns["m2"].vertical.name]:.3f}</div>\n'
    motors += f'              <div>m2_lateral, {md[user_ns["m2"].lateral.name]:.3f}</div>\n'
    motors += f'              <div>m2_pitch, {md[user_ns["m2"].pitch.name]:.3f}</div>\n'
    motors += f'              <div>m2_roll, {md[user_ns["m2"].roll.name]:.3f}</div>\n'
    motors += f'              <div>m2_yaw, {md[user_ns["m2"].yaw.name]:.3f}</div>\n'
    motors += f'              <div>m2_yu, {md[user_ns["m2"].yu.name]:.3f}</div>\n'
    motors += f'              <div>m2_ydo, {md[user_ns["m2"].ydo.name]:.3f}</div>\n'
    motors += f'              <div>m2_ydi, {md[user_ns["m2"].ydi.name]:.3f}</div>\n'
    motors += f'              <div>m2_xu, {md[user_ns["m2"].xu.name]:.3f}</div>\n'
    motors += f'              <div>m2_xd, {md[user_ns["m2"].xd.name]:.3f}</div>\n'
    motors += f'              <div>m2_bender, {md[user_ns["m2_bender"].name]:.3f}</div>\n'
    motors +=  '            </div>\n'

    stripe = '(Rh/Pt stripe)'
    if md[user_ns['m3'].xu.name] < 0:
        stripe = '(Si stripe)'
    motors += f'            <span class="motorheading">M3 {stripe}:</span>\n'
    motors +=  '            <div id="motorgrid">\n'
    motors += f'              <div>m3_vertical, {md[user_ns["m3"].vertical.name]:.3f}</div>\n'
    motors += f'              <div>m3_lateral, {md[user_ns["m3"].lateral.name]:.3f}</div>\n'
    motors += f'              <div>m3_pitch, {md[user_ns["m3"].pitch.name]:.3f}</div>\n'
    motors += f'              <div>m3_roll, {md[user_ns["m3"].roll.name]:.3f}</div>\n'
    motors += f'              <div>m3_yaw, {md[user_ns["m3"].yaw.name]:.3f}</div>\n'
    motors += f'              <div>m3_yu, {md[user_ns["m3"].yu.name]:.3f}</div>\n'
    motors += f'              <div>m3_ydo, {md[user_ns["m3"].ydo.name]:.3f}</div>\n'
    motors += f'              <div>m3_ydi, {md[user_ns["m3"].ydi.name]:.3f}</div>\n'
    motors += f'              <div>m3_xu, {md[user_ns["m3"].xu.name]:.3f}</div>\n'
    motors += f'              <div>m3_xd, {md[user_ns["m3"].xd.name]:.3f}</div>\n'
    motors +=  '            </div>\n'

    motors +=  '            <span class="motorheading">XAFS table:</span>\n'
    motors +=  '            <div id="motorgrid">\n'
    motors += f'              <div>xt_vertical, {md[user_ns["xafs_table"].vertical.name]:.3f}</div>\n'
    motors += f'              <div>xt_pitch, {md[user_ns["xafs_table"].pitch.name]:.3f}</div>\n'
    motors += f'              <div>xt_roll, {md[user_ns["xafs_table"].roll.name]:.3f}</div>\n'
    motors += f'              <div>xt_yu, {md[user_ns["xafs_table"].yu.name]:.3f}</div>\n'
    motors += f'              <div>xt_ydo, {md[user_ns["xafs_table"].ydo.name]:.3f}</div>\n'
    motors += f'              <div>xt_ydi, {md[user_ns["xafs_table"].ydi.name]:.3f}</div>\n'
    motors +=  '            </div>\n'

    
    motors +=  '            <span class="motorheading">Slits2:</span>\n'
    motors +=  '            <div id="motorgrid">\n'
    motors += f'              <div>slits2_vsize, {md[user_ns["slits2"].vsize.name]:.3f}</div>\n'
    motors += f'              <div>slits2_vcenter, {md[user_ns["slits2"].vcenter.name]:.3f}</div>\n'
    motors += f'              <div>slits2_hsize, {md[user_ns["slits2"].hsize.name]:.3f}</div>\n'
    motors += f'              <div>slits2_hcenter, {md[user_ns["slits2"].hcenter.name]:.3f}</div>\n'
    motors += f'              <div>slits2_top, {md[user_ns["slits2"].top.name]:.3f}</div>\n'
    motors += f'              <div>slits2_bottom, {md[user_ns["slits2"].bottom.name]:.3f}</div>\n'
    motors += f'              <div>slits2_inboard, {md[user_ns["slits2"].inboard.name]:.3f}</div>\n'
    motors += f'              <div>slits2_outboard, {md[user_ns["slits2"].outboard.name]:.3f}</div>\n'
    motors +=  '            </div>\n'

    motors +=  '            <span class="motorheading">Diagnostics:</span>\n'
    motors +=  '            <div id="motorgrid">\n'
    motors += f'              <div>dm3_foils, {md[user_ns["dm3_foils"].name]:.3f}</div>\n'
    motors += f'              <div>dm2_fs, {md[user_ns["dm2_fs"].name]:.3f}</div>\n'
    motors +=  '            </div>\n'

    
    # mlist = []
    # mlist.append('XAFS stages:')
    # mlist.append('xafs_x, %.3f, xafs_y, %.3f'         % (md[user_ns['xafs_x'].name],  md[user_ns['xafs_y'].name]))
    # mlist.append('xafs_pitch, %.3f, xafs_roll, %.3f'  % (md[user_ns['xafs_pitch'].name], md[user_ns['xafs_roll'].name]))
    # mlist.append('xafs_ref, %.3f, xafs_wheel, %.3f'   % (md[user_ns['xafs_refy'].name], md[user_ns['xafs_wheel'].name]))
    # mlist.append('xafs_garot, %.3f, xafs_det, %.3f'   % (md[user_ns['xafs_mtr8'].name],  md[user_ns['xafs_lins'].name]))
    # mlist.append('wheel slot = %2d'                   % user_ns['xafs_wheel'].current_slot())
    # mlist.append('glancing angle spinner = %2d'       % user_ns['ga'].current())
    # motors += '<br>\n&nbsp;&nbsp;&nbsp;'.join(mlist)

    # motors += '\n<br><br>dm3_bct: %.3f' % md[user_ns['dm3_bct'].name]

    # mlist = []
    # mlist.append('Slits3:')
    # mlist.append('slits3.vsize, %.3f, slits3.vcenter, %.3f'     % (md[user_ns['slits3'].vsize.name],    md[user_ns['slits3'].vcenter.name]))
    # mlist.append('slits3.hsize, %.3f, slits3.hcenter, %.3f'     % (md[user_ns['slits3'].hsize.name],    md[user_ns['slits3'].hcenter.name]))
    # mlist.append('slits3.top, %.3f, slits3.bottom, %.3f'        % (md[user_ns['slits3'].top.name],      md[user_ns['slits3'].bottom.name]))
    # mlist.append('slits3.outboard, %.3f, slits3.inboard, %.3f'  % (md[user_ns['slits3'].outboard.name], md[user_ns['slits3'].inboard.name]))
    # motors += '\n<br><br>' + '<br>\n&nbsp;&nbsp;&nbsp;'.join(mlist)

    # mlist = []
    # mlist.append('M2')
    # mlist.append('m2.vertical, %.3f, m2.yu, %.3f' % (md[user_ns['m2'].vertical.name], md[user_ns['m2'].yu.name]))
    # mlist.append('m2.lateral, %.3f, m2.ydo, %.3f' % (md[user_ns['m2'].lateral.name],  md[user_ns['m2'].ydo.name]))
    # mlist.append('m2.pitch, %.3f, m2.ydi, %.3f'   % (md[user_ns['m2'].pitch.name],    md[user_ns['m2'].ydi.name]))
    # mlist.append('m2.roll, %.3f, m2.xu, %.3f'     % (md[user_ns['m2'].roll.name],     md[user_ns['m2'].xu.name]))
    # mlist.append('m2.yaw, %.3f, m2.xd, %.3f'      % (md[user_ns['m2'].yaw.name],      md[user_ns['m2'].xd.name]))
    # mlist.append('m2.bender, %9.1f'               %  md[user_ns['m2_bender'].name])
    # motors += '\n<br><br>' + '<br>\n&nbsp;&nbsp;&nbsp;'.join(mlist)


    # mlist = []
    # stripe = '(Rh/Pt stripe)'
    # if md[user_ns['m3'].xu.name] < 0:
    #     stripe = '(Si stripe)'
    # mlist.append('M3  %s' % stripe)
    # mlist.append('m3.vertical, %.3f, m3.yu, %.3f' % (md[user_ns['m3'].vertical.name], md[user_ns['m3'].yu.name]))
    # mlist.append('m3.lateral, %.3f, m3.ydo, %.3f' % (md[user_ns['m3'].lateral.name],  md[user_ns['m3'].ydo.name]))
    # mlist.append('m3.pitch, %.3f, m3.ydi, %.3f'   % (md[user_ns['m3'].pitch.name],    md[user_ns['m3'].ydi.name]))
    # mlist.append('m3.roll, %.3f, m3.xu, %.3f'     % (md[user_ns['m3'].roll.name],     md[user_ns['m3'].xu.name]))
    # mlist.append('m3.yaw, %.3f, m3.xd, %.3f'      % (md[user_ns['m3'].yaw.name],      md[user_ns['m3'].xd.name]))
    # motors += '\n<br><br>' + '<br>\n&nbsp;&nbsp;&nbsp;'.join(mlist)

    mlist = []
    # mlist.append('XAFS table:')
    # mlist.append('xt.vertical, %.3f, xt.yu, %.3f' % (md[user_ns['xafs_table'].vertical.name], md[user_ns['xafs_table'].yu.name]))
    # mlist.append('xt.pitch, %.3f, xt.ydo, %.3f'   % (md[user_ns['xafs_table'].pitch.name],    md[user_ns['xafs_table'].ydo.name]))
    # mlist.append('xt.roll, %.3f, xt.ydi, %.3f'    % (md[user_ns['xafs_table'].roll.name],     md[user_ns['xafs_table'].ydi.name]))
    # motors += '\n<br><br>' + '<br>\n&nbsp;&nbsp;&nbsp;'.join(mlist)

    # mlist = []
    # mlist.append('Slits2:')
    # mlist.append('slits2.vsize, %.3f, slits2.vcenter, %.3f'    % (md[user_ns['slits2'].vsize.name],    md[user_ns['slits2'].vcenter.name]))
    # mlist.append('slits2.hsize, %.3f, slits2.hcenter, %.3f'    % (md[user_ns['slits2'].hsize.name],    md[user_ns['slits2'].hcenter.name]))
    # mlist.append('slits2.top, %.3f, slits2.bottom, %.3f'       % (md[user_ns['slits2'].top.name],      md[user_ns['slits2'].bottom.name]))
    # mlist.append('slits2.outboard, %.3f, slits2.inboard, %.3f' % (md[user_ns['slits2'].outboard.name], md[user_ns['slits2'].inboard.name]))
    # motors += '\n<br><br>' + '<br>\n&nbsp;&nbsp;&nbsp;'.join(mlist)

    # motors += '\n<br><br>dm3_foils, %.3f' % md[user_ns['dm3_foils'].name]
    # motors += '\n<br>dm2_fs, %.3f' % md[user_ns['dm2_fs'].name]

    
    return motors
