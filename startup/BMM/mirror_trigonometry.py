
from bluesky.plan_stubs import null, sleep, mv, mvr
from numpy import tan, pi
from BMM.motor_status  import motor_status

#run_report(__file__, text='mirror trigonometry')

from bmm_tools.tools.animated_prompt import PROMPTNC
from BMM.user_ns.instruments import *
from BMM.user_ns.motors      import *

def move_m3(target=5):
    alpha = 9002                # distance M3 to Be window
    beta  = 9637                # distance M3 to xafs_yu
    gamma = 10792               # distance M3 to xafs_ydo/xafs_ydi

    #bnot = 46.11                # BCT position in mode D/E
    #unot = 132                  # xafs_yu position in mode D/E
    #dnot = 132                  # xafs_ydo/xafs_ydi position in mode D/E
    bnot = 29.57                # BCT position in mode D/E
    unot = 111.6                  # xafs_yu position in mode D/E
    dnot = 109.9                  # xafs_ydo/xafs_ydi position in mode D/E

    thetanot = 0                # angle in mR of M3 in mode A
    #thetanot = 3.5             # angle in mR of M3 in mode D/E

    theta = target - thetanot

    #correction = 0.8 * tan(theta/1000) / tan(1.5/1000)
    correction = 0.0 * tan(theta/1000) / tan(1.5/1000)

    bct   = bnot - alpha * tan( (2*theta) / 1000 ) + correction - 0.6
    upstr = unot - beta  * tan( (2*theta) / 1000 ) + correction
    dnstr = dnot - gamma * tan( (2*theta) / 1000 ) + correction

    print('\nThe M3 target is %.2f mRad relative to the beam incident on M3\n' % target)
    print('Move M3 pitch to %.2f mRad' % (thetanot - theta))
    print('\t BCT:          %.2f' % bct)
    print('\t xafs_yu:      %.2f' % upstr)
    print('\t xafs_yd:      %.2f' % dnstr)
    print('\t (correction): %.2f' % correction)
    print('')

    action = input("Begin moving motors? " + PROMPTNC)
    if action != '':
        if action[0].lower() == 'n' or action[0].lower() == 'q':
            yield from null()
            return

    RE.msg_hook = None
    BMM_log_info('Moving mirror 3: target = %.2f, M3 pitch = %.2f\nBCT -> %.2f, yu -> %.2f, yd -> %.2f, correction = %.2f'
                 % (target, thetanot-theta, bct, upstr, dnstr, correction))

    yield from mv(dm3_bct.kill_cmd, 1) # and after

    yield from mv(m3.pitch,       thetanot-theta,
                  dm3_bct,        bct,
                  xafs_table.yu,  upstr,
                  xafs_table.ydo, dnstr,
                  xafs_table.ydi, dnstr)

    yield from sleep(2.0)
    yield from mv(dm3_bct.kill_cmd, 1) # and after
    RE.msg_hook = BMM_msg_hook
    BMM_log_info(motor_status())


def move_m2(target=3.5):
    alpha = 10907               # distance M2 to Be window
    beta  = 11542               # distance M2 to xafs_yu
    gamma = 12697               # distance M2 to xafs_ydo/xafs_ydi

    bnot = 50.2                # BCT position in mode A
    unot = 136-0.83                  # xafs_yu position in mode A
    dnot = 136-0.92                  # xafs_ydo/xafs_ydi position in mode D/E
    #bnot = 35.14                # BCT position in mode A
    #unot = 120.04                  # xafs_yu position in mode A
    #dnot = 117.96                  # xafs_ydo/xafs_ydi position in mode D/E

    thetanot = 3.5              # angle in mR of M2 in mode A
    #thetanot = 4.428              # angle in mR of M2 in mode A

    theta = target - thetanot

    correction = 0 # 0.8 * tan(theta/1000) / tan(1.5/1000)

    bct   = bnot - alpha * tan( (2*theta) / 1000 ) + correction + 1.218
    upstr = unot - beta  * tan( (2*theta) / 1000 ) + correction
    dnstr = dnot - gamma * tan( (2*theta) / 1000 ) + correction

    print('\nThe M2 target is %.2f mRad relative to the beam incident on M2\n' % target)
    print('Move M2 pitch to %.2f mRad' % (thetanot - theta))
    print('\t BCT:          %.2f' % bct)
    print('\t xafs_yu:      %.2f' % upstr)
    print('\t xafs_yd:      %.2f' % dnstr)
    print('\t (correction): %.2f' % correction)
    print('')

    action = input("Begin moving motors? " + PROMPTNC)
    if action != '':
        if action[0].lower() == 'n' or action[0].lower() == 'q':
            yield from null()
            return

    RE.msg_hook = None
    BMM_log_info('Moving mirror 2: target = %.2f, M2 pitch = %.2f\nBCT -> %.2f, yu -> %.2f, yd -> %.2f, correction = %.2f'
                 % (target, thetanot-theta, bct, upstr, dnstr, correction))

    yield from mv(dm3_bct.kill_cmd, 1)

    yield from mv(m2.pitch,       thetanot-theta,
                  dm3_bct,        bct,
                  xafs_table.yu,  upstr,
                  xafs_table.ydo, dnstr,
                  xafs_table.ydi, dnstr)

    yield from sleep(2.0)
    yield from mv(dm3_bct.kill_cmd, 1) # and after
    RE.msg_hook = BMM_msg_hook
    BMM_log_info(motor_status())
