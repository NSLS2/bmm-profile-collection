import json, time, os

from openpyxl import load_workbook

from bluesky.plan_stubs import null, sleep, mv, mvr

from BMMCommon.tools.messages import *  # error_msg et al. + boxedtext
from BMMCommon.tools.animated_prompt import PROMPTNC, animated_prompt

from BMM.exceptions    import ChangeModeException
from BMM.functions     import approximate_pitch, countdown, PROMPT
from BMM.kafka         import kafka_message
from BMM.linescans     import rocking_curve, slit_height, mirror_pitch, wiggle_bct
from BMM.logging       import BMM_log_info, BMM_msg_hook, report
from BMM.motor_status  import motor_status
from BMM.resting_state import resting_state_plan
from BMM.suspenders    import BMM_clear_to_start

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from BMM.user_ns.base   import profile_configuration


MODEDATA = None
def read_mode_data():
     '''Read the lookup table Modes.xlsx and return position and encoder
     readings as a dict.
     '''
     wb = load_workbook(os.path.join(user_ns["BMM_CONFIGURATION_LOCATION"], 'Modes.xlsx'), read_only=True);
     ws = wb['Modes A-F']
     bl = dict()
     header = 1
     for row in ws.rows:
         axis = dict()
         if str(row[0].value) == 'Instrument':
             header = 0
             continue
         if header == 1: continue
         alias           = row[2].value
         if 'fe_slits' in alias: continue
         axis['PV']      = row[1].value
         axis['desc']    = row[3].value
         axis['A']       = row[4].value
         axis['A_REP']   = row[5].value
         axis['B']       = row[6].value
         axis['B_REP']   = row[7].value
         axis['C']       = row[8].value
         axis['C_REP']   = row[9].value
         axis['D']       = row[10].value
         axis['D_REP']   = row[11].value
         axis['E']       = row[12].value
         axis['E_REP']   = row[13].value
         axis['F']       = row[14].value
         axis['F_REP']   = row[15].value
         axis['XRD']     = row[19].value
         axis['XRD_REP'] = row[20].value
         bl[alias] = axis
     return bl

MODEDATA = read_mode_data();

#     return json.load(open(os.path.join(user_ns["BMM_CONFIGURATION_LOCATION"], 'Modes.json')))
#if os.path.isfile(os.path.join(user_ns["BMM_CONFIGURATION_LOCATION"], 'Modes.json')):
#     MODEDATA = read_mode_data()

def motors_in_position(mode=None):
    motors = ['dm3_bct',
              'xafs_yu', 'xafs_ydo', 'xafs_ydi',
              'm2_yu', 'm2_ydo', 'm2_ydi', #'m2_xu', 'm2_xd',
              'm3_yu', 'm3_ydo', 'm3_ydi', 'm3_xu', 'm3_xd',]
    ok = True
    for m in motors:
        target = float(MODEDATA[user_ns[m].name][mode])
        achieved = user_ns[m].position
        diff = abs(target - achieved)
        if diff > 0.5:
            print(f'{m} is out of position, target={target}, current position={achieved}')
            ok = False
    return ok


def pds_motors_ready():
    m3, m2, m2_bender, dm3_bct = user_ns['m3'], user_ns['m2'], user_ns['m2_bender'], user_ns['dm3_bct']
    dcm_pitch, dcm_roll, dcm_perp, dcm_roll, dcm_bragg = user_ns["dcm_pitch"], user_ns["dcm_roll"], user_ns["dcm_perp"], user_ns["dcm_roll"], user_ns["dcm_bragg"]
    mcs8_motors = [m3.xu, m3.xd, m3.yu, m3.ydo, m3.ydi, m2.xu, m2.xd, m2.yu, m2.ydo, m2.ydi, m2_bender,
                   dcm_pitch, dcm_roll, dcm_perp, dcm_roll, dcm_bragg, dm3_bct]

    count = 0
    for m in mcs8_motors:
        if m.amfe.get() or m.amfae.get():
            error_msg("%-12s : %s / %s" % (m.name, m.amfe.enum_strs[m.amfe.get()], m.amfae.enum_strs[m.amfae.get()]))
            count += 1
        else:
            pass
    if count > 0:
        return(False)
    else:
        return(True)

# def pds_mirrors_cycle():
#    ready_count = 0
#    while pds_motors_ready() is False:
#        ready_count += 1
#        report('\nOne or more motors are showing amplifier faults. Attempting to correct the problem.', level='error', slack=True)
#        problem_is_m2 = False
#        m2_yu, m2_ydo, m2_ydi = user_ns['m2_yu'], user_ns['m2_ydo'], user_ns['m2_ydi']
#        m2_xu, m2_xd, m2_bender = user_ns['m2_xu'], user_ns['m2_xd'], user_ns['m2_bender']
#        for m in (m2.xu, m2.xd, m2.yu, m2.ydo, m2.ydi, m2_bender):
#            if m.amfe.get() or m.amfae.get():
#                problem_is_m2 = True
#        if problem_is_m2 is True:
#            user_ns['ks'].cycle('m2')

#        problem_is_m3 = False
#        m3_yu, m3_ydo, m3_ydi = user_ns['m2_yu'], user_ns['m2_ydo'], user_ns['m2_ydi']
#        m3_xu, m3_xd = user_ns['m2_xu'], user_ns['m2_xd']
#        for m in (m3.xu, m3.xd, m3.yu, m3.ydo, m3.ydi):
#            if m.amfe.get() or m.amfae.get():
#                problem_is_m3 = True
#        if problem_is_m3 is True:
#            user_ns['ks'].cycle('m3')

#        # problem_is_dcm = False
#        # dcm_pitch, dcm_roll, dcm_perp = user_ns['dcm_pitch'], user_ns['dcm_roll'], user_ns['dcm_perp']
#        # dcm_para, dcm_bragg = user_ns['dcm_para'], user_ns['dcm_bragg']
#        # for m in (dcm_pitch, dcm_roll, dcm_perp, dcm_para, dcm_bragg):
#        #     if m.amfe.get() or m.amfae.get():
#        #         problem_is_dcm = True
#        # if problem_is_dcm is True:
#        #     user_ns['ks'].cycle('dcm')

#        if ready_count > 5:
#            report('Failed to fix an amplifier fault while changing mode.', level='error', slack=True)
#            yield from null()
#            return
     

def table_height(mode=None, by=None, pitch=None):
     '''Move the XAS table to the correct height and inclination for the
     specified mode, or adjust the table height, or adjust the table pitch.

     absolute
     ========
     Move the table to the recorded table height for a photon delivery mode

        table_height(mode=MODE)

     where MODE is one of ('A', 'B', 'C', 'D', 'E', 'F', 'XRD')

     relative
     ========
     Adjust the table height by an amount

        table_height(by=AMOUNT)

     where AMOUNT is a number (float or int)

     pitch
     =====
     Adjust the ends of the table by opposite amounts

        table_height(pitch=ADJUSTMENT)

     where ADJUSTMENT is a number (float or int)
     The back of the table will be adjusted by that value, 
     the front of the table by -1 times that value, thus
     modifying the pitch of the table.  Note: this is units
     of millimeters, not degrees.
     '''
     xafs_table = user_ns['xafs_table']
     if by is not None:
          yield from mvr(xafs_table.yu,  float(by),
                         xafs_table.ydo, float(by),
                         xafs_table.ydi, float(by))
     elif pitch is not None:
          yield from mvr(xafs_table.yu,  -1 * float(pitch),
                         xafs_table.ydo, float(pitch),
                         xafs_table.ydi, float(pitch))
     elif mode in ('A', 'B', 'C', 'D', 'E', 'F', 'XRD'):
          yield from mv(xafs_table.yu,   float(MODEDATA['xafs_yu'][mode]),
                        xafs_table.ydo,  float(MODEDATA['xafs_ydo'][mode]),
                        xafs_table.ydi,  float(MODEDATA['xafs_ydi'][mode]))
     else:
          print('Doing nothing.  Do table_height?? for explanation')
          yield from null()


def verify_limits(targets):
     '''Take the list of mv() arguments generated in change_mode and check
     each motor target position against that motor's limit.

     This list is an awkward data structure for this application:
 
       base = [dm3_bct,         float(MODEDATA['dm3_bct'][mode]),
               xafs_table.yu,   float(MODEDATA['xafs_yu'][mode]),
               xafs_table.ydo,  float(MODEDATA['xafs_ydo'][mode]),
               xafs_table.ydi,  float(MODEDATA['xafs_ydi'][mode]),
               ...
              ]

     This structure explains the awkward looping over list elements.

     '''

     is_ok = True
     for i in range(int(len(targets)/2)):
          #print(targets[2*i].name, targets[2*i+1])
          motor = targets[2*i]
          motorname = targets[2*i].name
          value = targets[2*i+1]
          if value < motor.limits[0]:
               error_msg(f'Target value for {motorname} ({value}) is outside the lower limit of {motor.limits[0]}')
               is_ok = False
          if value > motor.limits[1]:
               error_msg(f'Target value for {motorname} ({value}) is outside the upper limit of {motor.limits[1]}')
               is_ok = False
     return is_ok
     
          

def change_mode(mode=None, prompt=True, edge=None, reference=None, bender=True, insist=False, no_ref=False):
     '''Move the photon delivery system to a new mode. 
     A: focused at XAS end station, energy > 8000
     B: focused at XAS end station, energy < 6000
     C: focused at XAS end station, 6000 < energy < 8000
     D: unfocused, energy > 8000
     E: unfocused, 6000 < energy < 8000
     F: unfocused, energy < 8000
     XRD: focused at XRD end station, energy > 8000
     '''
     BMMuser, RE, dcm, dm3_bct, slits3 = user_ns['BMMuser'], user_ns['RE'], user_ns['dcm'], user_ns['dm3_bct'], user_ns['slits3']
     xafs_table, m3, m2, m2_bender = user_ns['xafs_table'], user_ns['m3'], user_ns['m2'], user_ns['m2_bender']
     m2_xu, m2_xd = user_ns['m2_xu'], user_ns['m2_xd']
     dcm_bragg, dcm_roll, xafs_ref, xafs_refx = user_ns['dcm_bragg'], user_ns['dcm_roll'], user_ns['xafs_ref'], user_ns['xafs_refx']
     if mode is None:
          print('No mode specified')
          return(yield from null())

     mode = mode.upper()
     if mode not in ('A', 'B', 'C', 'D', 'E', 'F', 'XRD'):
          print('%s is not a mode' % mode)
          return(yield from null())
     current_mode = get_mode()


     # crude hack around a problem I don't understand
     if dm3_bct.hlm.get() < 55 or dm3_bct.llm.get() > -55:
          dm3_bct.llm.put(-60)
          dm3_bct.hlm.put(60)

     if pds_motors_ready() is False:
          report('\nOne or more motors are showing amplifier faults.\nCycle the correct kill switch, then try again.', level='error', slack=True)
          error_msg('One or more motors are showing amplifier faults. (in BMM/modes.py)')
          count = 0
          while pds_motors_ready() is False:
               count += 1
               if count == 5:
                    report('\nFailed to correct the problem. Giving up.)', level='error', slack=True)
                    yield from null()
                    return
               report('\nAmplifier fault? Attempting to correct the problem. (Try #{count})', level='error', slack=True)
               yield from sleep(1)
               m2_bender.kill()
               user_ns['ks'].cycle('m2')
               user_ns['ks'].cycle('m3')
               user_ns['ks'].cycle('dm3')
               dm3_bct.clear_encoder_loss()
               yield from sleep(1)
          #raise ChangeModeException('One or more motors are showing amplifier faults. (in BMM/modes.py)')
          #return(yield from null())

     ######################################################################
     # this is a tool for verifying a macro.  this replaces an xafs scan  #
     # with a sleep, allowing the user to easily map out motor motions in #
     # a macro                                                            #
     if BMMuser.macro_dryrun:
          info_msg('\nBMMuser.macro_dryrun is True.  Sleeping for %.1f seconds rather than changing to mode %s.\n' %
                   (BMMuser.macro_sleep, mode))
          countdown(BMMuser.macro_sleep)
          return(yield from null())
     ######################################################################

     if mode == 'B':
          #action = input("You are entering Mode B -- focused beam below 6 keV cannot be properly configured for harmonic rejection. Continue? " + PROMPT)
          print()
          action = animated_prompt('You are entering Mode B -- focused beam below 6 keV cannot be properly configured for harmonic rejection. Continue? ' + PROMPTNC)
          if action !='' and action[0].lower() != 'y':
               return(yield from null())

     if mode == 'A':
          description = 'focused, >8 keV'
     elif mode == 'B':
          description = 'focused, <6 keV'
     elif mode == 'C':
          description = 'focused, 6 to 8 keV'
     elif mode == 'D':
          description = 'unfocused, >8 keV'
     elif mode == 'E':
          description = 'unfocused, 6 to 8 keV'
     elif mode == 'F':
          description = 'unfocused, <6 keV'
     elif mode == 'XRD':
          description = 'focused at goniometer, >8 keV'
          print('Moving to mode %s (%s)' % (mode, description))
     if prompt:
          #action = input("Begin moving motors? " + PROMPT)
          print()
          action = animated_prompt('Begin moving motors? ' + PROMPTNC)
          if action != '':
               if action[0].lower() == 'n' or action[0].lower() == 'q':
                    return(yield from null())
          
     RE.msg_hook = None
     BMM_log_info('Changing photon delivery system to mode %s' % mode)

     base = [dm3_bct,         float(MODEDATA['dm3_bct'][mode]),

             xafs_table.yu,   float(MODEDATA['xafs_yu'][mode]),
             xafs_table.ydo,  float(MODEDATA['xafs_ydo'][mode]),
             xafs_table.ydi,  float(MODEDATA['xafs_ydi'][mode]),

             m3.yu,           float(MODEDATA['m3_yu'][mode]),
             m3.ydo,          float(MODEDATA['m3_ydo'][mode]),
             m3.ydi,          float(MODEDATA['m3_ydi'][mode]),
             m3.xu,           float(MODEDATA['m3_xu'][mode]),
             m3.xd,           float(MODEDATA['m3_xd'][mode]), ]

     if profile_configuration.getboolean('experiments', 'use_reference') is False:
          no_ref = False
          reference = None
     if reference is not None:
          #base.extend([xafs_linxs, foils.position(reference.capitalize())])
          #base.extend([xafs_ref, xafs_ref.position_of_slot(reference.capitalize())])
          if reference.capitalize() in xafs_ref.mapping:
               slot = xafs_ref.mapping[reference.capitalize()][1]
               if xafs_ref.mapping[reference.capitalize()][0] == 1:
                    ring = xafs_ref.inner_position
               else:
                    ring = xafs_ref.outer_position
          else:
               slot = 1
               ring = xafs_ref.outer_position
          if no_ref is False:
               base.extend([xafs_ref, xafs_ref.position_of_slot(slot), xafs_refx, ring])
          #xafs_refx.user_setpoint.set(ring) # ick!!!

     if edge is not None:
          dcm_bragg.clear_encoder_loss()
          base.extend([dcm.energy, edge])

     
     ###################################################################
     # check for amplifier faults on the motors, return without moving #
     # anything if any are found                                       #
     ###################################################################
     motors_ready = True
     problem_motors = list()
     for m in base[::2]:
          try:        # skip non-FMBO motors, which do not have the amfe or amfae attributes
               if m.amfe.get() == 1 or m.amfae.get() == 1:
                    motors_ready = False
                    problem_motors.append(m.name)
          except:
               continue
     if motors_ready is False:
          BMMuser.motor_fault = ', '.join(problem_motors)
          report('One or more motors are failing amplifier fault checks.', level='error', slack=True)
          raise ChangeModeException('One or more motors are failing amplifier fault checks. (in BMM/modes.py)')
          #return (yield from null())

     ##########################
     # do the motor movements #
     ##########################
     # yield from dcm.kill_plan()
     # if dm3_bct.ampen.get() == 0:
     #      yield from mv(dm3_bct.enable_cmd, 1)
     # #yield from mv(dm3_bct.kill_cmd, 1) # need to explicitly kill this before
     #                                    # starting a move, it is one of the
     #                                    # motors that reports MOVN=1 even when
     #                                    # still
     # yield from sleep(0.2)
     # yield from mv(dm3_bct.kill_cmd, 1)
     yield from wiggle_bct()

     
     #print(mode, current_mode, insist)

     report(f'Moving from mode {current_mode} to mode {mode}', slack=True)

     if mode == 'XRD':
          print('For XRD mode, move to old (pre 4/2025) position for dcm_roll.')
          yield from mv(dcm_roll, -4.5608)
     else:
          print('For all XAS modes, move to new (post 4/2025) position for dcm_roll.')
          yield from  mv(dcm_roll, profile_configuration.getfloat('dcm', f'roll_{dcm._crystal}'))
          
     if mode in ('D', 'E', 'F') and user_ns['slits3'].vsize.position < 0.4:
          print('Slit height appears to be set for focused beam.  Opening slits.')
          yield from mv(user_ns['slits3'].vsize, 1.0)
     elif mode in ('A', 'B', 'C') and user_ns['slits3'].vsize.position > 0.5:
          print('Slit height appears to be set for collimated beam.  Narrowing slits.')
          yield from mv(user_ns['slits3'].vsize, 0.3)
     
     if mode in ('D', 'E', 'F') and current_mode in ('D', 'E', 'F') and insist is False:
          if verify_limits(base) is False:
               bold_msg('Cannot change mode. Some target values are outside motor limits.')
               raise ValueError
          try:
               report('M2 remaining collimated', slack=True)
               yield from mv(*base)
          except Exception as E:
               verbosebold_msg(f"\nThis is the problem:\n\t{E}\n")
               count = 0
               while motors_in_position(mode) is False:
                    count += 1
                    if count > 5:
                         report('\nTried five times to correct the amplifier fault.  Giving up now.',
                                level='error', slack=True)
                         return
                    report(f'\nAmplifier fault? Attempting to correct the problem. (Attempt {count})',
                           level='error', slack=True)
                    yield from sleep(1)
                    user_ns['ks'].cycle('m2')
                    user_ns['ks'].cycle('m3')
                    user_ns['ks'].cycle('dm3')
                    yield from wiggle_bct()
                    #dm3_bct.clear_encoder_loss()
                    yield from sleep(1)
                    try:
                         yield from mv(*base)
                    except:
                         pass
               
     elif mode in ('A', 'B', 'C') and current_mode in ('A', 'B', 'C') and insist is False: # no need to move M2
          if verify_limits(base) is False:
               bold_msg('Cannot change mode. Some target values are outside motor limits.')
               raise ValueError
          try:
               report('M2 remaining focused', slack=True)
               yield from mv(*base)
          except Exception as E:
               verbosebold_msg(f"\nThis is the problem:\n\t{E}\n")
               count = 0
               while motors_in_position(mode) is False:
                    count += 1
                    if count > 5:
                         report('\nTried five times to correct the amplifier fault.  Giving up now.',
                                level='error', slack=True)
                         return
                    report(f'\nAmplifier fault? Attempting to correct the problem. (Attempt {count})',
                           level='error', slack=True)
                    yield from sleep(1)
                    user_ns['ks'].cycle('m2')
                    user_ns['ks'].cycle('m3')
                    user_ns['ks'].cycle('dm3')
                    yield from wiggle_bct()
                    #dm3_bct.clear_encoder_loss()
                    yield from sleep(1)
                    try:
                         yield from mv(*base)
                    except:
                         pass
     else:
          if bender is True:
               yield from mv(m2_bender.kill_cmd, 1)
               if mode == 'XRD':
                    if abs(m2_bender.user_readback.get() - BMMuser.bender_xrd) > BMMuser.bender_margin: # give some wiggle room for having
                         base.extend([m2_bender, BMMuser.bender_xrd])                                   # recently adjusted the bend
                    base.extend([m2_xu,  0.091])
                    base.extend([m2_xd,  0.315])
               elif mode in ('A', 'B', 'C'):
                    if abs(m2_bender.user_readback.get() - BMMuser.bender_xas) > BMMuser.bender_margin:
                         base.extend([m2_bender, BMMuser.bender_xas])
                    base.extend([m2_xu, -1.015])
                    base.extend([m2_xd, -0.779])

          base.extend([m2.yu,  float(MODEDATA['m2_yu'][mode])])
          base.extend([m2.ydo, float(MODEDATA['m2_ydo'][mode])])
          base.extend([m2.ydi, float(MODEDATA['m2_ydi'][mode])])
          if verify_limits(base) is False:
               bold_msg('Cannot change mode. Some target values are outside motor limits.')
               raise ValueError
          try:
               report('Changing M2 setup', slack=True)
               yield from mv(*base)
          except Exception as E:
               verbosebold_msg(f"\nThis is the problem:\n\t{E}\n")
               count = 0
               while motors_in_position(mode) is False:
                    count += 1
                    if count > 5:
                         report('\nTried five times to correct the amplifier fault.  Giving up now.',
                                level='error', slack=True)
                         return
                    report(f'\nAmplifier fault? Attempting to correct the problem. (Attempt {count})',
                           level='error', slack=True)
                    yield from sleep(1)
                    m2_bender.kill()
                    user_ns['ks'].cycle('m2')
                    user_ns['ks'].cycle('m3')
                    user_ns['ks'].cycle('dm3')
                    yield from wiggle_bct()
                    #dm3_bct.clear_encoder_loss()
                    yield from sleep(1)
                    try:
                         yield from mv(*base)
                    except:
                         pass

     #print(base)
                    
     yield from sleep(1.0)
     yield from mv(m2_bender.kill_cmd, 1)
     yield from mv(dm3_bct.kill_cmd, 1)
     yield from m2.kill_jacks()
     yield from m3.kill_jacks()
     yield from dcm.kill_plan()

     BMMuser.pds_mode = mode
     RE.msg_hook = BMM_msg_hook
     BMM_log_info(motor_status())


def mode():
    print('Motor positions:')
    for m in ('dm3_bct',
              'xafs_yu', 'xafs_ydo', 'xafs_ydi',
              'm2_yu', 'm2_ydo',
              'm2_ydi', 'm2_bender', 'm3_yu', 'm3_ydo', 'm3_ydi', 'm3_xu', 'm3_xd',
              'dm3_slits_t', 'dm3_slits_b', 'dm3_slits_i', 'dm3_slits_o'):
        mot = user_ns[m]
        print('\t%-12s:\t%.3f' % (mot.name, mot.user_readback.get()))
        
    m2, m3 = user_ns['m2'], user_ns['m3']
    if m2.vertical.readback.get() < 0: # this is a focused mode
        if m2.pitch.readback.get() > 3:
            print('This appears to be mode XRD')
        else:
            if m3.vertical.readback.get() > -2:
                print(f'This appears to be mode A ({describe_mode()})')
            elif m3.vertical.readback.get() > -7:
                print(f'This appears to be mode B ({describe_mode()})')
            else:
                print(f'This appears to be mode C ({describe_mode()})')
    else:
        if m3.pitch.readback.get() < 3:
            print(f'This appears to be mode F ({describe_mode()})')
        elif m3.lateral.readback.get() > 0:
            print(f'This appears to be mode D ({describe_mode()})')
        else:
            print(f'This appears to be mode E ({describe_mode()})')


def get_mode():
    m2, m3 = user_ns['m2'], user_ns['m3']
    if m2.vertical.readback.get() < 0: # this is a focused mode
        if m2.pitch.readback.get() > 3:
            return 'XRD'
        else:
            if m3.vertical.readback.get() > -2:
                return 'A'
            elif m3.vertical.readback.get() > -7:
                return 'B'
            else:
                return 'C'
    else:
        if m3.pitch.readback.get() < 3:
            return 'F'
        elif m3.lateral.readback.get() > 0:
            return 'D'
        else:
            return 'E'

def describe_mode():
    m2, m3 = user_ns['m2'], user_ns['m3']
    if m2.vertical.readback.get() < 0: # this is a focused mode
        if m2.pitch.readback.get() > 3:
            return 'focused at goniometer, >8 keV'
        else:
            if m3.vertical.readback.get() > -2:
                return 'focused, >8 keV'
            elif m3.vertical.readback.get() > -7:
                return 'focused, <6 keV'
            else:
                return 'focused, 6 to 8 keV'
    else:
        if m3.pitch.readback.get() < 3:
            return 'unfocused, <6 keV'
        elif m3.lateral.readback.get() > 0:
            return 'unfocused, >8 keV'
        else:
            return 'unfocused, 6 to 8 keV'



def change_xtals(xtal=None):
     '''Move between the Si(111) and Si(311) monochromators, also moving
     2nd crystal pitch and roll to approximate positions.  Return to
     the starting energy.  Then do a rocking curve scan.
     '''
     if xtal is None:
          print('No crystal set specified')
          return(yield from null())

     (ok, text) = BMM_clear_to_start()
     if ok == 0:
          error_msg(text)
          yield from null()
          return

     BMMuser, RE, dcm, dm3_bct = user_ns['BMMuser'], user_ns['RE'], user_ns['dcm'], user_ns['dm3_bct']
     dcm_pitch, dcm_roll, dcm_x = user_ns['dcm_pitch'], user_ns['dcm_roll'], user_ns['dcm_x']
     
     if '111' in xtal:
          xtal = 'Si(111)'
     if '311' in xtal:
          xtal = 'Si(311)'

     if xtal not in ('Si(111)', 'Si(311)'):
          print('%s is not a crytsal set' % xtal)
          return(yield from null())

     if pds_motors_ready() is False:
          error_msg('\nOne or more motors are showing amplifier faults.\nToggle the correct kill switch, then re-enable the faulted motor.')
          return(yield from null())

     
     ######################################################################
     # this is a tool for verifying a macro.  this replaces an xafs scan  #
     # with a sleep, allowing the user to easily map out motor motions in #
     # a macro                                                            #
     if BMMuser.macro_dryrun:
          info_msg('\nBMMuser.macro_dryrun is True.  Sleeping for %.1f seconds rather than changing to the %s crystal.\n' %
                   (BMMuser.macro_sleep, xtal))
          countdown(BMMuser.macro_sleep)
          return(yield from null())
     ######################################################################

     report(f'Moving to {xtal} crystals', level='bold', slack=True, rid=True)
     #action = input('Begin moving motors? ' + PROMPT)
     print()
     action = animated_prompt('Begin moving motors? ' + PROMPTNC)
     if action != '':
          if action[0].lower() == 'n' or action[0].lower() == 'q':
               yield from null()
               return

     current_energy = dcm.energy.readback.get()

     ## make sure the starting energy is sensible for the mono being moved to
     if xtal == 'Si(111)' and current_energy > 20000:
          yield from mv(dcm.energy, 20000)
     elif xtal == 'Si(311)' and current_energy < 5500:
          yield from mv(dcm.energy, 5500)
     
     start = time.time()

     RE.msg_hook = None
     BMM_log_info('Moving to the %s crystals' % xtal)
     #yield from mv(dcm_pitch.kill_cmd, 1)
     #yield from mv(dcm_roll.kill_cmd, 1)
     yield from dcm.kill_plan()
     yield from sleep(1.0) 
     if xtal == 'Si(111)':
          yield from mv(dcm_pitch, 4.3,
                        dcm_roll,  profile_configuration.getfloat('dcm', 'roll_111'),
                        dcm_x,     0.5    )
          #dcm._crystal = '111'
          dcm.set_crystal('111')  # set d-spacing and bragg offset
     elif xtal == 'Si(311)':
          yield from mv(dcm_pitch, 2.28,
                        dcm_roll,  profile_configuration.getfloat('dcm', 'roll_311'),
                        dcm_x,     65.3    )
          #dcm._crystal = '311'
          dcm.set_crystal('311')  # set d-spacing and bragg offset
          
     yield from sleep(1.0) 
     yield from dcm.kill_plan()
     #yield from mv(dcm_roll.kill_cmd, 1)

     print('Returning to %.1f eV' % current_energy)
     yield from mv(dcm.energy, current_energy)

     print('Performing a rocking curve scan')
     yield from mv(dcm_pitch.kill_cmd, 1)
     yield from mv(dcm_pitch, approximate_pitch(current_energy))
     yield from sleep(1)
     yield from mv(dcm_pitch.kill_cmd, 1)
     yield from rocking_curve()
     yield from sleep(1.0)
     yield from mv(dcm_pitch.kill_cmd, 1)
     kafka_message({'close': 'line'})
     #yield from slit_height(move=True)
     yield from mirror_pitch(move=True)
     RE.msg_hook = BMM_msg_hook
     BMM_log_info(motor_status())
     kafka_message({'close': 'line'})
     end = time.time()
     print('\n\nTime elapsed: %.1f min' % ((end-start)/60))
     yield from sleep(1.0)
     yield from resting_state_plan()
     report(f'Done moving to {xtal} crystals', level='bold', slack=True)
     
