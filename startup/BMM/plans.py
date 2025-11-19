from bluesky.plan_stubs import sleep, mv, mvr
import time

from rich import print as cprint

from BMMCommon.tools.messages import *  # error_msg et al. + boxedtext

from BMM.modes import MODEDATA

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)


# for use in queue server
def move(motor, absolute_position):
    '''
    A thin wrapper around a single axis absolute move for use in queueserver
    '''
    yield from mv(motor, absolute_position)

def mover(motor, relative_position):
    '''
    A thin wrapper around a single axis relative move for use in queueserver
    '''
    yield from mvr(motor, relative_position)


TUNE_STEP = 0.004
def tune_plan(step=0):
    '''
    Tune 2nd crystal pitch from a plan.  Argument is a value for the step, so a relative motion.
    '''
    yield from mv(dcm_pitch.kill_cmd, 1)
    yield from mvr(dcm_pitch, step)
    yield from sleep(1.0)
    yield from mv(dcm_pitch.kill_cmd, 1)
def tune_up():
    yield from tune_plan(step=TUNE_STEP)
def tune_down():
    yield from tune_plan(step=-1*TUNE_STEP)

def tune(step=0):
    '''
    Tune 2nd crystal pitch from the command line.  Argument is a value for the step, so a relative motion.
    '''
    dcm_pitch = user_ns['dcm_pitch']
    dcm_pitch.kill_cmd.put(1)
    dcm_pitch.user_setpoint.put(dcm_pitch.user_readback.get() + step)
    time.sleep(2.0)
    dcm_pitch.kill_cmd.put(1)
def tu():
    tune(step=TUNE_STEP)
def td():
    tune(step=-1*TUNE_STEP)

def tweak_bct(step):
    dm3_bct = user_ns['dm3_bct']
    if step is None:
        step = 0
    if dm3_bct.ampen.get() == 0:
        yield from mv(dm3_bct.enable_cmd, 1)     
    yield from mv(dm3_bct.kill_cmd, 1)
    print('Moving from %.4f to %.4f' % (dm3_bct.user_readback.get(), dm3_bct.user_readback.get() + step))
    yield from mvr(dm3_bct, step)
    #time.sleep(3.0)
    #yield from mv(dm3_bct.kill_cmd, 1)



def kmv(*args):
    for m in args[0::2]:
        if 'Vacuum' in str(type(m)):
            yield from mv(m.kill_cmd, 1)
    yield from mv(*args)

def kmvr(*args):
    for m in args[0::2]:
        if 'Vacuum' in str(type(m)):
            yield from mv(m.kill_cmd, 1)
    yield from mvr(*args)



def recover_slits2():
    inb  = user_ns['dm2_slits_i']
    outb = user_ns['dm2_slits_o']
    top  = user_ns['dm2_slits_t']
    bot  = user_ns['dm2_slits_b']
    slits2 = user_ns['slits2']
    yield from mv(inb.home_signal,  1)
    yield from mv(top.home_signal,  1)
    yield from sleep(1.0)
    print('Begin homing slits2 inboard/outboard & top/bottom:\n')
    hvalues = (inb.hocpl.get(), outb.hocpl.get(), top.hocpl.get(), bot.hocpl.get())
    while any(v == 0 for v in hvalues):
        hvalues = (inb.hocpl.get(), outb.hocpl.get(), top.hocpl.get(), bot.hocpl.get())
        strings = ['dm2_slits_i', 'dm2_slits_o', 'dm2_slits_t', 'dm2_slits_b']
        for i,v in enumerate(hvalues):
            strings[i] = f'[green]{strings[i]}[/green]' if hvalues[i] == 1 else f'[red1]{strings[i]}[/red1]'
        cprint('  '.join(strings), end='\r')
        yield from sleep(1.0)
    print('\n')
    yield from mv(slits2.vsize, 1.1, slits2.hsize, 18)


def recover_slits3():
    inb  = user_ns['dm3_slits_i']
    outb = user_ns['dm3_slits_o']
    top  = user_ns['dm3_slits_t']
    bot  = user_ns['dm3_slits_b']
    slits3 = user_ns['slits3']
    yield from mv(inb.home_signal,  1)
    yield from mv(top.home_signal,  1)
    yield from sleep(1.0)
    print('Begin homing slits3 inboard/outboard & top/bottom:\n')
    hvalues = (inb.hocpl.get(), outb.hocpl.get(), top.hocpl.get(), bot.hocpl.get())
    while any(v == 0 for v in hvalues):
        hvalues = (inb.hocpl.get(), outb.hocpl.get(), top.hocpl.get(), bot.hocpl.get())
        strings = ['dm3_slits_i', 'dm3_slits_o', 'dm3_slits_t', 'dm3_slits_b']
        for i,v in enumerate(hvalues):
            strings[i] = f'[green]{strings[i]}[/green]' if hvalues[i] == 1 else f'[red1]{strings[i]}[/red1]'
        cprint('  '.join(strings), end='\r')
        yield from sleep(1.0)
    print('\n')
    yield from mv(slits3.vsize,   1, slits3.hsize,   5)
    yield from mv(slits3.vcenter, 0, slits3.hcenter, 0)

    
def recover_diagnostics():
    dm2_fs    = user_ns['dm2_fs']
    dm3_fs    = user_ns['dm3_fs']
    dm3_bct   = user_ns['dm3_bct']
    dm3_bpm   = user_ns['dm3_bpm']
    dm3_foils = user_ns['dm3_foils']
    yield from mv(dm2_fs.home_signal,  1)
    yield from mv(dm3_fs.home_signal,  1)
    yield from mv(dm3_bct.home_signal, 1)
    yield from mv(dm3_bpm.home_signal, 1)
    yield from mv(dm3_foils.home_signal, 1)
    yield from sleep(1.0)
    print('Begin homing dm2_fs, dm3_fs, dm3_bct, dm3_bpm, and dm3_foils:\n')
    hvalues = (dm2_fs.hocpl.get(), dm3_fs.hocpl.get(), dm3_bct.hocpl.get(), dm3_bpm.hocpl.get(), dm3_foils.hocpl.get())
    while any(v == 0 for v in hvalues):
        hvalues = (dm2_fs.hocpl.get(), dm3_fs.hocpl.get(), dm3_bct.hocpl.get(), dm3_bpm.hocpl.get(), dm3_foils.hocpl.get())
        strings = ['dm2_fs', 'dm3_fs', 'dm3_bct', 'dm3_bpm', 'dm3_foils']
        for i,v in enumerate(hvalues):
            strings[i] = f'[green]{strings[i]}[/green]' if hvalues[i] == 1 else f'[red1]{strings[i]}[/red1]'
        cprint('  '.join(strings), end='\r')
        yield from sleep(1.0)
    print('\n')
    #yield from mv(dm3_bct.kill_cmd, 1)
    yield from mv(dm3_foils.kill_cmd, 1)
    yield from sleep(1.0)
    ## take these from Modes.xlsx, mode D -- all out of the beam path
    yield from mv(dm2_fs, 67, dm3_fs, 55, dm3_bct, 43.6565, dm3_bpm, 5.511, dm3_foils, 41)

    
def recover_mirror2():
    m2_xu, m2_xd, m2_yu, m2_ydo, m2_ydi = user_ns['m2_xu'], user_ns['m2_xd'], user_ns['m2_yu'], user_ns['m2_ydo'], user_ns['m2_ydi']
    yield from mv(m2_xu.home_signal,  1) # xu and xd home together
    yield from mv(m2_yu.home_signal,  1) # yu, ydi, and ydo home together
    yield from sleep(1.0)
    print('Begin homing lateral and vertical motors in M2:\n')
    hvalues = (m2_yu.hocpl.get(), m2_ydo.hocpl.get(), m2_ydi.hocpl.get(), m2_xu.hocpl.get(), m2_xd.hocpl.get())
    while any(v == 0 for v in hvalues):
        hvalues = (m2_yu.hocpl.get(), m2_ydo.hocpl.get(), m2_ydi.hocpl.get(), m2_xu.hocpl.get(), m2_xd.hocpl.get())
        strings = ['m2_yu', 'm2_ydo', 'm2_ydi', 'm2_xu', 'm2_xd',]
        for i,v in enumerate(hvalues):
            strings[i] = f'[green]{strings[i]}[/green]' if hvalues[i] == 1 else f'[red1]{strings[i]}[/red1]'
        cprint('  '.join(strings), end='\r')
        yield from sleep(1.0)
    print('\n')
    yield from mv(m2_yu,  MODEDATA['m2_yu']['E'],
                  m2_ydo, MODEDATA['m2_ydo']['E'],
                  m2_ydi, MODEDATA['m2_ydi']['E'],
                  m2_xu,  MODEDATA['m2_xu']['E'],
                  m2_xd,  MODEDATA['m2_xd']['E'])

def recover_mirror3():
    m3_xu, m3_xd, m3_yu, m3_ydo, m3_ydi = user_ns['m3_xu'], user_ns['m3_xd'], user_ns['m3_yu'], user_ns['m3_ydo'], user_ns['m3_ydi']
    yield from mv(m3_xu.home_signal,  1) # xu and xd home together
    yield from mv(m3_ydi.home_signal,  1) # yu, ydi, and ydo home together
    yield from sleep(1.0)
    print('Begin homing lateral and vertical motors in M3:\n')
    hvalues = (m3_yu.hocpl.get(), m3_ydo.hocpl.get(), m3_ydi.hocpl.get(), m3_xu.hocpl.get(), m3_xd.hocpl.get())
    while any(v == 0 for v in hvalues):
        hvalues = (m3_yu.hocpl.get(), m3_ydo.hocpl.get(), m3_ydi.hocpl.get(), m3_xu.hocpl.get(), m3_xd.hocpl.get())
        strings = ['m3_yu', 'm3_ydo', 'm3_ydi', 'm3_xu', 'm3_xd',]
        for i,v in enumerate(hvalues):
            strings[i] = f'[green]{strings[i]}[/green]' if hvalues[i] == 1 else f'[red1]{strings[i]}[/red1]'
        cprint('  '.join(strings), end='\r')
        yield from sleep(1.0)
    print('\n')
    yield from mv(m3_yu,  MODEDATA['m3_yu']['E'],
                  m3_ydo, MODEDATA['m3_ydo']['E'],
                  m3_ydi, MODEDATA['m3_ydi']['E'],
                  m3_xu,  MODEDATA['m3_xu']['E'],
                  m3_xd,  MODEDATA['m3_xd']['E'])


def recover_mirrors():
    m2_xu, m2_xd, m2_yu, m2_ydo, m2_ydi = user_ns['m2_xu'], user_ns['m2_xd'], user_ns['m2_yu'], user_ns['m2_ydo'], user_ns['m2_ydi']
    m3_xu, m3_xd, m3_yu, m3_ydo, m3_ydi = user_ns['m3_xu'], user_ns['m3_xd'], user_ns['m3_yu'], user_ns['m3_ydo'], user_ns['m3_ydi']
    yield from mv(m2_yu.home_signal,  1)
    yield from mv(m2_xu.home_signal,  1)
    yield from mv(m3_yu.home_signal,  1)
    yield from mv(m3_xu.home_signal,  1)
    yield from sleep(1.0)
    print('Begin homing lateral and vertical motors in M2 and M3:\n')
    hvalues = (m2_yu.hocpl.get(), m2_ydo.hocpl.get(), m2_ydi.hocpl.get(), m2_xu.hocpl.get(), m2_xd.hocpl.get(),
               m3_yu.hocpl.get(), m3_ydo.hocpl.get(), m3_ydi.hocpl.get(), m3_xu.hocpl.get(), m3_xd.hocpl.get())
    while any(v == 0 for v in hvalues):
        hvalues = (m2_yu.hocpl.get(), m2_ydo.hocpl.get(), m2_ydi.hocpl.get(), m2_xu.hocpl.get(), m2_xd.hocpl.get(),
                   m3_yu.hocpl.get(), m3_ydo.hocpl.get(), m3_ydi.hocpl.get(), m3_xu.hocpl.get(), m3_xd.hocpl.get())
        strings = ['m2_yu', 'm2_ydo', 'm2_ydi', 'm2_xu', 'm2_xd', 'm3_yu', 'm3_ydo', 'm3_ydi', 'm3_xu', 'm3_xd',]
        for i,v in enumerate(hvalues):
            strings[i] = f'[green]{strings[i]}[/green]' if hvalues[i] == 1 else f'[red1]{strings[i]}[/red1]'
        cprint('  '.join(strings), end='\r')
        yield from sleep(1.0)
    print('\n')
    yield from mv(m2_yu,  MODEDATA['m2_yu']['E'],
                  m2_ydo, MODEDATA['m2_ydo']['E'],
                  m2_ydi, MODEDATA['m2_ydi']['E'],
                  m2_xu,  MODEDATA['m2_xu']['E'],
                  m2_xd,  MODEDATA['m2_xd']['E'],
                  m3_yu,  MODEDATA['m3_yu']['E'],
                  m3_ydo, MODEDATA['m3_ydo']['E'],
                  m3_ydi, MODEDATA['m3_ydi']['E'],
                  m3_xu,  MODEDATA['m3_xu']['E'],
                  m3_xd,  MODEDATA['m3_xd']['E'])





def mvbct(target=None):
    '''
    A workaround to kill the BCT motor, then do an absolute movement
    '''
    dm3_bct = user_ns['dm3_bct']
    if target is None:
        target = dm3_bct.user_readback.get()
    if dm3_bct.ampen.get() == 0:
        yield from mv(dm3_bct.enable_cmd, 1)
    yield from mv(dm3_bct.kill_cmd, 1)
    yield from mv(dm3_bct, target)

def mvrbct(target=None):
    '''
    A workaround to kill the BCT motor, then do a relative movement

    Since Jan 27 2022, this should not be necessary.
    '''
    dm3_bct = user_ns['dm3_bct']
    if target is None:
        target = 0
    if dm3_bct.ampen.get() == 0:
        yield from mv(dm3_bct.enable_cmd, 1)     
    yield from mv(dm3_bct.kill_cmd, 1)
    yield from mvr(dm3_bct, target)


def mvbender(target=None):
    '''
    A workaround to kill the M2 bender motor, then do an absolute movement

    Since Jan 27 2022, this should not be necessary.
    '''
    m2_bender = user_ns['m2_bender']
    if target is None:
        target = m2_bender.user_readback.get()
    yield from mv(m2_bender.kill_cmd, 1)
    yield from mv(m2_bender, target)

def mvrbender(target=None):
    '''
    A workaround to kill the M2 bender motor, then do a relative movement
    '''
    m2_bender = user_ns['m2_bender']
    if target is None:
        target = 0
    yield from mv(m2_bender.kill_cmd, 1)
    yield from mvr(m2_bender, target)
    
