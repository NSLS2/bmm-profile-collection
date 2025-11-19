from ophyd import (EpicsMotor, PseudoPositioner, PseudoSingle, Signal, Device, PositionerBase, Component as Cpt, EpicsSignal, EpicsSignalRO)
from ophyd.epics_motor import EpicsMotor, required_for_connection, AlarmSeverity
from ophyd.utils.epics_pvs import fmt_time
from ophyd.pseudopos import (pseudo_position_argument,
                             real_position_argument)

from BMMCommon.tools.messages import *  # error_msg et al. + boxedtext

from bluesky.plan_stubs import sleep, mv, null, abs_set
from BMM.functions import PROMPT
from BMM.logging   import BMM_log_info

import time

status_list = {'MTACT' : 1, 'MLIM'  : 0, 'PLIM'  : 0, 'AMPEN' : 0,
               'LOOPM' : 1, 'TIACT' : 0, 'INTMO' : 1, 'DWPRO' : 0,
               'DAERR' : 0, 'DVZER' : 0, 'ABDEC' : 0, 'UWPEN' : 0,
               'UWSEN' : 0, 'ERRTG' : 0, 'SWPOC' : 0, 'ASSCS' : 1,
               'FRPOS' : 0, 'HSRCH' : 0, 'SODPL' : 0, 'SOPL'  : 0,
               'HOCPL' : 1, 'PHSRA' : 0, 'PREFE' : 0, 'TRMOV' : 0,
               'IFFE'  : 0, 'AMFAE' : 0, 'AMFE'  : 0, 'FAFOE' : 0,
               'WFOER' : 0, 'INPOS' : 1, 'ENC_LSS' : 0}


# for DeadbandEpicsMotor and DeadbandEpicsMotor see
# https://github.com/xraygui/nbs-bl/blob/master/nbs_bl/devices/motors.py#L123
# Thanks to Jamie Titus

class DeadbandMixin(Device, PositionerBase):
    """
    Should be the leftmost class in the inheritance list so that it grabs move first!

    Must be combined with either EpicsMotor or PVPositioner, or some other class
    that has a done_value attribute

    An EpicsMotor subclass that has an absolute tolerance for moves.
    If the readback is within tolerance of the setpoint, the MoveStatus
    is marked as finished, even if the motor is still settling.

    This prevents motors with long, but irrelevant, settling times from
    adding overhead to scans.
    """

    tolerance = Cpt(Signal, value=-1, kind="omitted")
    move_latch = Cpt(Signal, value=0, kind="omitted")

    def __init__(self, *args, tolerance=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tolerance is not None:
            self.tolerance.put(tolerance)

    def _done_moving(self, success=True, timestamp=None, value=None, **kwargs):
        """Call when motion has completed.  Runs ``SUB_DONE`` subscription."""
        if self.move_latch.get():
            #print(f"{timestamp} {datetime.today().time().isoformat()} [{self.name}]: marked done")
            if success:
                self._run_subs(sub_type=self.SUB_DONE, timestamp=timestamp, value=value)

            self._run_subs(
                sub_type=self._SUB_REQ_DONE, success=success, timestamp=timestamp
            )
            self._reset_sub(self._SUB_REQ_DONE)
            self.move_latch.put(0)

    def move(self, position, wait=True, **kwargs):
        tolerance = self.tolerance.get()

        if tolerance < 0:
            self.move_latch.put(1)
            return super().move(position, wait=wait, **kwargs)
        else:
            status = super().move(position, wait=False, **kwargs)
            setpoint = position
            done_value = getattr(self, "done_value", 1)

            def check_deadband(value, timestamp, **kwargs):
                if abs(value - setpoint) < tolerance:
                    #print(f"{timestamp} {datetime.today().time().isoformat()} [{self.name}]: {value} within {tolerance} of {setpoint}")
                    self._done_moving(
                        timestamp=timestamp, success=True, value=done_value
                    )
                else:
                    pass
                    # print(f"{timestamp}: {self.name}, {value} not within {tolerance} of {setpoint}")

            def clear_deadband(*args, **kwargs):
                # print(f"{timestamp}: Ran deadband clear for {self.name}")
                self.clear_sub(check_deadband, event_type=self.SUB_READBACK)

            self.subscribe(clear_deadband, event_type=self._SUB_REQ_DONE, run=False)
            self.move_latch.put(1)
            self.subscribe(check_deadband, event_type=self.SUB_READBACK, run=True)

            try:
                if wait:
                    status_wait(status)
            except KeyboardInterrupt:
                self.stop()
                raise

            return status


class DeadbandEpicsMotor(DeadbandMixin, EpicsMotor):
    """
    An EpicsMotor subclass that has an absolute tolerance for moves.
    If the readback is within tolerance of the setpoint, the MoveStatus
    is marked as finished, even if the motor is still settling.

    This prevents motors with long, but irrelevant, settling times from
    adding overhead to scans.

    This class is designed to be subclassed.
    """

    pass




class EpicsMotorWithDial(EpicsMotor):
    dial = Cpt(EpicsSignal, '.DVAL', kind='omitted')
    def dmv(self, absolute_value):
        yield from mv(self.dial, absolute_value)
    def dmvr(self, relative_value):
        absolute_value = self.dial.get() + relative_value
        yield from mv(self.dial, absolute_value)

    
class FMBOEpicsMotor(EpicsMotor):
#class FMBOEpicsMotor(DeadbandEpicsMotor):
    resolution = Cpt(EpicsSignal, '.MRES', kind = 'omitted')
    encoder = Cpt(EpicsSignal, '.REP', kind = 'omitted')
    
    ###################################################################
    # this is the complete list of status signals defined in the FMBO #
    # IOC for thier MCS8 motor controllers                            #
    ###################################################################
    # mtact      = Cpt(EpicsSignal, '_MTACT_STS',      kind = 'omitted')
    # mtact_desc = Cpt(EpicsSignal, '_MTACT_STS.DESC', kind = 'omitted')
    # mlim       = Cpt(EpicsSignal, '_MLIM_STS',       kind = 'omitted')
    # mlim_desc  = Cpt(EpicsSignal, '_MLIM_STS.DESC',  kind = 'omitted')
    # plim       = Cpt(EpicsSignal, '_PLIM_STS',       kind = 'omitted')
    # plim_desc  = Cpt(EpicsSignal, '_PLIM_STS.DESC',  kind = 'omitted')
    ampen      = Cpt(EpicsSignal, '_AMPEN_STS',      kind = 'omitted')
    # ampen_desc = Cpt(EpicsSignal, '_AMPEN_STS.DESC', kind = 'omitted')
    # loopm      = Cpt(EpicsSignal, '_LOOPM_STS',      kind = 'omitted')
    # loopm_desc = Cpt(EpicsSignal, '_LOOPM_STS.DESC', kind = 'omitted')
    # tiact      = Cpt(EpicsSignal, '_TIACT_STS',      kind = 'omitted')
    # tiact_desc = Cpt(EpicsSignal, '_TIACT_STS.DESC', kind = 'omitted')
    # intmo      = Cpt(EpicsSignal, '_INTMO_STS',      kind = 'omitted')
    # intmo_desc = Cpt(EpicsSignal, '_INTMO_STS.DESC', kind = 'omitted')
    # dwpro      = Cpt(EpicsSignal, '_DWPRO_STS',      kind = 'omitted')
    # dwpro_desc = Cpt(EpicsSignal, '_DWPRO_STS.DESC', kind = 'omitted')
    # daerr      = Cpt(EpicsSignal, '_DAERR_STS',      kind = 'omitted')
    # daerr_desc = Cpt(EpicsSignal, '_DAERR_STS.DESC', kind = 'omitted')
    # dvzer      = Cpt(EpicsSignal, '_DVZER_STS',      kind = 'omitted')
    # dvzer_desc = Cpt(EpicsSignal, '_DVZER_STS.DESC', kind = 'omitted')
    # abdec      = Cpt(EpicsSignal, '_ABDEC_STS',      kind = 'omitted')
    # abdec_desc = Cpt(EpicsSignal, '_ABDEC_STS.DESC', kind = 'omitted')
    # uwpen      = Cpt(EpicsSignal, '_UWPEN_STS',      kind = 'omitted')
    # uwpen_desc = Cpt(EpicsSignal, '_UWPEN_STS.DESC', kind = 'omitted')
    # uwsen      = Cpt(EpicsSignal, '_UWSEN_STS',      kind = 'omitted')
    # uwsen_desc = Cpt(EpicsSignal, '_UWSEN_STS.DESC', kind = 'omitted')
    # errtg      = Cpt(EpicsSignal, '_ERRTG_STS',      kind = 'omitted')
    # errtg_desc = Cpt(EpicsSignal, '_ERRTG_STS.DESC', kind = 'omitted')
    # swpoc      = Cpt(EpicsSignal, '_SWPOC_STS',      kind = 'omitted')
    # swpoc_desc = Cpt(EpicsSignal, '_SWPOC_STS.DESC', kind = 'omitted')
    # asscs      = Cpt(EpicsSignal, '_ASSCS_STS',      kind = 'omitted')
    # asscs_desc = Cpt(EpicsSignal, '_ASSCS_STS.DESC', kind = 'omitted')
    # frpos      = Cpt(EpicsSignal, '_FRPOS_STS',      kind = 'omitted')
    # frpos_desc = Cpt(EpicsSignal, '_FRPOS_STS.DESC', kind = 'omitted')
    # hsrch      = Cpt(EpicsSignal, '_HSRCH_STS',      kind = 'omitted')
    # hsrch_desc = Cpt(EpicsSignal, '_HSRCH_STS.DESC', kind = 'omitted')
    # sodpl      = Cpt(EpicsSignal, '_SODPL_STS',      kind = 'omitted')
    # sodpl_desc = Cpt(EpicsSignal, '_SODPL_STS.DESC', kind = 'omitted')
    # sopl       = Cpt(EpicsSignal, '_SOPL_STS',       kind = 'omitted')
    # sopl_desc  = Cpt(EpicsSignal, '_SOPL_STS.DESC',  kind = 'omitted')
    hocpl      = Cpt(EpicsSignal, '_HOCPL_STS',      kind = 'omitted')
    # hocpl_desc = Cpt(EpicsSignal, '_HOCPL_STS.DESC', kind = 'omitted')
    # phsra      = Cpt(EpicsSignal, '_PHSRA_STS',      kind = 'omitted')
    # phsra_desc = Cpt(EpicsSignal, '_PHSRA_STS.DESC', kind = 'omitted')
    # prefe      = Cpt(EpicsSignal, '_PREFE_STS',      kind = 'omitted')
    # prefe_desc = Cpt(EpicsSignal, '_PREFE_STS.DESC', kind = 'omitted')
    # trmov      = Cpt(EpicsSignal, '_TRMOV_STS',      kind = 'omitted')
    # trmov_desc = Cpt(EpicsSignal, '_TRMOV_STS.DESC', kind = 'omitted')
    # iffe       = Cpt(EpicsSignal, '_IFFE_STS',       kind = 'omitted')
    # iffe_desc  = Cpt(EpicsSignal, '_IFFE_STS.DESC',  kind = 'omitted')
    amfae      = Cpt(EpicsSignal, '_AMFAE_STS',      kind = 'omitted')
    # amfae_desc = Cpt(EpicsSignal, '_AMFAE_STS.DESC', kind = 'omitted')
    amfe       = Cpt(EpicsSignal, '_AMFE_STS',       kind = 'omitted')
    # amfe_desc  = Cpt(EpicsSignal, '_AMFE_STS.DESC',  kind = 'omitted')
    #fafoe      = Cpt(EpicsSignal, '_FAFOE_STS',      kind = 'omitted')
    # fafoe_desc = Cpt(EpicsSignal, '_FAFOE_STS.DESC', kind = 'omitted')
    #wfoer      = Cpt(EpicsSignal, '_WFOER_STS',      kind = 'omitted')
    # wfoer_desc = Cpt(EpicsSignal, '_WFOER_STS.DESC', kind = 'omitted')
    #inpos      = Cpt(EpicsSignal, '_INPOS_STS',      kind = 'omitted')
    # inpos_desc = Cpt(EpicsSignal, '_INPOS_STS.DESC', kind = 'omitted')

    enc_lss       = Cpt(EpicsSignal, '_ENC_LSS_STS', kind = 'omitted')
    # enc_lss_desc  = Cpt(EpicsSignal, '_ENC_LSS_STS.DESC', kind = 'omitted')
    clear_enc_lss = Cpt(EpicsSignal, '_ENC_LSS_CLR_CMD.PROC', kind = 'omitted')
    
    home_signal = Cpt(EpicsSignal, '_HOME_CMD.PROC', kind = 'omitted')
    hvel_sp     = Cpt(EpicsSignal, '_HVEL_SP.A', kind = 'omitted') # how homing velocity gets set for an FMBO SAI

    @required_for_connection
    @EpicsMotor.motor_done_move.sub_value
    def _move_changed(self, timestamp=None, value=None, sub_type=None,
                      **kwargs):
        '''Callback from EPICS, indicating that movement status has changed'''
        was_moving = self._moving
        self._moving = (value != 1)

        started = False
        if not self._started_moving:
            started = self._started_moving = (not was_moving and self._moving)

        self.log.debug('[ts=%s] %s moving: %s (value=%s)', fmt_time(timestamp),
                       self, self._moving, value)

        if started:
            self._run_subs(sub_type=self.SUB_START, timestamp=timestamp,
                           value=value, **kwargs)

        if was_moving and not self._moving:
            success = True
            # Check if we are moving towards the low limit switch
            if self.direction_of_travel.get() == 0:
                if self.low_limit_switch.get() == 1:
                    success = False
            # No, we are going to the high limit switch
            else:
                if self.high_limit_switch.get() == 1:
                    success = False

            # Check the severity of the alarm field after motion is complete.
            # If there is any alarm at all warn the user, and if the alarm is
            # greater than what is tolerated, mark the move as unsuccessful
            severity = self.user_readback.alarm_severity

            if severity != AlarmSeverity.NO_ALARM:
                status = self.user_readback.alarm_status
                if severity > self.tolerated_alarm:
                    self.log.error('Motion failed: %s is in an alarm state '
                                   'status=%s severity=%s',
                                   self.name, status, severity)
                    if 'm3' in self.name and 'dm3' not in self.name:
                        which = 'm3'
                    elif 'm2' in self.name and 'dm2' not in self.name:
                        which = 'm2'
                    elif 'dcm' in self.name:
                        which = 'dcm'
                    elif 'slits2' in self.name:
                        which = 'slits2'
                    elif 'slits3' in self.name or 'dm3' in self.name:
                        which = 'dm3'

                    error_msg('\n\n' + f'try doing:  ks.cycle(\'{which}\')')
                    print('Then try your last command again.  If this error repeats, call for help.')
                    whisper('The possible arguments for ks.cycle() are m3, m2, dcm, slits2, and dm3\n')
                    success = False
                else:
                    self.log.warning('Motor %s raised an alarm during motion '
                                     'status=%s severity %s',
                                     self.name, status, severity)
            self._done_moving(success=success, timestamp=timestamp,
                              value=value)
    
    
    def status(self):
        text = '\n  [white]%s is %s[/white]\n\n' % (self.name, self.prefix)
        for signal in status_list.keys():
            sig = signal.lower()
            try:
                suffix = getattr(self, sig).pvname.replace(self.prefix, '')
                string = getattr(self, sig).enum_strs[getattr(self, sig).get()]
                text += '  %-19s  %s   %s \n' % (string,
                                                 '[white]' + str(getattr(self, sig).get()) + '[/white]',
                                                 '[bold black]' + suffix + '[/bold black]')
            except:
                pass
        boxedtext(text, title=f'{self.name} status signals', color='green')

    def home(self, force=False):
        if force is False:
            action = input(f"\nBegin homing {self.name}? " + PROMPT)
            if action != '':
                if action[0].lower() == 'n' or action[0].lower() == 'q':
                    return
        self.home_signal.put(1)

    def clear_encoder_loss(self):
        BMM_log_info('clearing encoder loss for %s' % self.name)
        self.clear_enc_lss.put(1)
        self.enable()

    def wh(self):
        return(round(self.user_readback.get(), 3))

class FMBOThinEpicsMotor(EpicsMotor):
    hlm = Cpt(EpicsSignal, '.HLM', kind='omitted')
    llm = Cpt(EpicsSignal, '.LLM', kind='omitted')
    kill_cmd = Cpt(EpicsSignal, '_KILL_CMD.PROC', kind='omitted')
    enable_cmd = Cpt(EpicsSignal, '_ENA_CMD.PROC', kind='omitted')

    @required_for_connection
    @EpicsMotor.motor_done_move.sub_value
    def _move_changed(self, timestamp=None, value=None, sub_type=None,
                      **kwargs):
        '''Callback from EPICS, indicating that movement status has changed'''
        was_moving = self._moving
        self._moving = (value != 1)

        started = False
        if not self._started_moving:
            started = self._started_moving = (not was_moving and self._moving)

        self.log.debug('[ts=%s] %s moving: %s (value=%s)', fmt_time(timestamp),
                       self, self._moving, value)

        if started:
            self._run_subs(sub_type=self.SUB_START, timestamp=timestamp,
                           value=value, **kwargs)

        if was_moving and not self._moving:
            success = True
            # Check if we are moving towards the low limit switch
            if self.direction_of_travel.get() == 0:
                if self.low_limit_switch.get() == 1:
                    success = False
            # No, we are going to the high limit switch
            else:
                if self.high_limit_switch.get() == 1:
                    success = False

            # Check the severity of the alarm field after motion is complete.
            # If there is any alarm at all warn the user, and if the alarm is
            # greater than what is tolerated, mark the move as unsuccessful
            severity = self.user_readback.alarm_severity

            if severity != AlarmSeverity.NO_ALARM:
                status = self.user_readback.alarm_status
                if severity > self.tolerated_alarm:
                    self.log.error('Motion failed: %s is in an alarm state '
                                   'status=%s severity=%s',
                                   self.name, status, severity)
                    if 'm3' in self.name and 'dm3' not in self.name:
                        which = 'm3'
                    elif 'm2' in self.name and 'dm2' not in self.name:
                        which = 'm2'
                    elif 'dcm' in self.name:
                        which = 'dcm'
                    elif 'slits2' in self.name:
                        which = 'slits2'
                    elif 'slits3' in self.name or 'dm3' in self.name:
                        which = 'dm3'

                    error_msg('\n\n' + f'try doing:  ks.cycle(\'{which}\')')
                    print('Then try your last command again.  If this error repeats, call for help.')
                    whisper('The possible arguments for ks.cycle() are m3, m2, dcm, slits2, and dm3\n')
                    success = False
                else:
                    self.log.warning('Motor %s raised an alarm during motion '
                                     'status=%s severity %s',
                                     self.name, status, severity)
            self._done_moving(success=success, timestamp=timestamp,
                              value=value)

    def kill(self):
        self.kill_cmd.put(1)
    def enable(self):
        self.enable_cmd.put(1)
        
    
class XAFSEpicsMotor(FMBOEpicsMotor):
    hlm = Cpt(EpicsSignal, '.HLM', kind='omitted')
    llm = Cpt(EpicsSignal, '.LLM', kind='omitted')
    kill_cmd = Cpt(EpicsSignal, '_KILL_CMD.PROC', kind='omitted')
    enable_cmd = Cpt(EpicsSignal, '_ENA_CMD.PROC', kind='omitted')


    @required_for_connection
    @EpicsMotor.motor_done_move.sub_value
    def _move_changed(self, timestamp=None, value=None, sub_type=None,
                      **kwargs):
        '''Callback from EPICS, indicating that movement status has changed'''
        was_moving = self._moving
        self._moving = (value != 1)

        started = False
        if not self._started_moving:
            started = self._started_moving = (not was_moving and self._moving)

        self.log.debug('[ts=%s] %s moving: %s (value=%s)', fmt_time(timestamp),
                       self, self._moving, value)

        if started:
            self._run_subs(sub_type=self.SUB_START, timestamp=timestamp,
                           value=value, **kwargs)

        if was_moving and not self._moving:
            success = True
            # Check if we are moving towards the low limit switch
            if self.direction_of_travel.get() == 0:
                if self.low_limit_switch.get() == 1:
                    success = False
            # No, we are going to the high limit switch
            else:
                if self.high_limit_switch.get() == 1:
                    success = False

            # Check the severity of the alarm field after motion is complete.
            # If there is any alarm at all warn the user, and if the alarm is
            # greater than what is tolerated, mark the move as unsuccessful
            severity = self.user_readback.alarm_severity

            if severity != AlarmSeverity.NO_ALARM:
                status = self.user_readback.alarm_status
                if severity > self.tolerated_alarm:
                    self.log.error('Motion failed: %s is in an alarm state '
                                   'status=%s severity=%s',
                                   self.name, status, severity)
                    if 'm3' in self.name and 'dm3' not in self.name:
                        which = 'm3'
                    elif 'm2' in self.name and 'dm2' not in self.name:
                        which = 'm2'
                    elif 'dcm' in self.name:
                        which = 'dcm'
                    elif 'slits2' in self.name:
                        which = 'slits2'
                    elif 'slits3' in self.name or 'dm3' in self.name:
                        which = 'dm3'

                    error_msg('\n\n' + f'try doing:  ks.cycle(\'{which}\')')
                    print('Then try your last command again.  If this error repeats, call for help.')
                    whisper('The possible arguments for ks.cycle() are m3, m2, dcm, slits2, and dm3\n')
                    success = False
                else:
                    self.log.warning('Motor %s raised an alarm during motion '
                                     'status=%s severity %s',
                                     self.name, status, severity)
            self._done_moving(success=success, timestamp=timestamp,
                              value=value)
    def kill(self):
        self.kill_cmd.put(1)
    def stop_and_kill(self):
        self.stop()
        self.kill_cmd.put(1)
        
    def enable(self):
        self.enable_cmd.put(1)
        
    
    #def wh(self):
    #    return(round(self.user_readback.get(), 3))


class BMMDeadBandMotor(DeadbandEpicsMotor, XAFSEpicsMotor):
    pass

    
class VacuumEpicsMotor(FMBOEpicsMotor):
    hlm = Cpt(EpicsSignal, '.HLM', kind='omitted')
    llm = Cpt(EpicsSignal, '.LLM', kind='omitted')
    kill_cmd = Cpt(EpicsSignal, '_KILL_CMD.PROC', kind='omitted')
    enable_cmd = Cpt(EpicsSignal, '_ENA_CMD.PROC', kind='omitted')

    #def wh(self):
    #    return(round(self.user_readback.get(), 3))

    # def _setup_move(self, *args):
    #     self.kill_cmd.put(1)
    #     super()._setup_move(*args)
        
    def set(self, position, **kwargs):
        self.kill_cmd.put(1)
        return super().set(position, **kwargs)
        
    def _done_moving(self, *args, **kwargs):
        ## this method is originally defined as Positioner, a base class of EpicsMotor
        ## tack on instructions for killing the motor after movement
        super()._done_moving(*args, **kwargs)
        time.sleep(0.05)
        self.kill_cmd.put(1)

class EndStationEpicsMotor(EpicsMotor):
    hlm = Cpt(EpicsSignal, '.HLM', kind='omitted')
    llm = Cpt(EpicsSignal, '.LLM', kind='omitted')
    kill_cmd = Cpt(EpicsSignal, ':KILL', kind='omitted')
    spmg = Cpt(EpicsSignal, '.SPMG', kind='omitted')
    setpoint = Cpt(EpicsSignal, '.VAL', kind='omitted')

    def wh(self):
        return(round(self.user_readback.get(), 3))

    def reset_limits(self):
        '''Reset motor limits if default limit values are explicitly defined.'''
        if hasattr(self, 'default_llm'):
            self.llm.put(self.default_llm)
        if hasattr(self, 'default_hlm'):
            self.hlm.put(self.default_hlm)

    def ready(self):
        '''Sometimes a motor gets into a paused state, usually by user
        interaction.  Simply unpausing might not be the right call.
        If the set point is different from the current position, the
        motor will begin moving as soon as it is re-enabled.

        This method forces the set point to the current position, then
        sets the SPMG filed for the motor to 3 (the "go" value from
        Stop/Pause/Move/Go).

        '''
        if abs(self.setpoint.get() - self.position) > 0.01:
            self.setpoint.put(self.position)
        self.spmg.put(3)
    
class EncodedEndStationEpicsMotor(EndStationEpicsMotor):
    def homed(self):
        'Return a string ("Homed"/"Not homed") indicating home status'
        home_status = EpicsSignal(self.prefix.replace('Mtr','') + 'Sts:HomeCmplt-Sts', name='')
        return home_status.get(as_string=True)
    


            
from numpy import tan, arctan2


class Mirrors(PseudoPositioner):
    def __init__(self, *args, mirror_length, mirror_width, **kwargs):
        self.mirror_length = mirror_length
        self.mirror_width  = mirror_width
        super().__init__(*args, **kwargs)

    def _done_moving(self, *args, **kwargs):
        ## this method is originally defined as Positioner, a base class of EpicsMotor
        ## tack on instructions for killing the motor after movement
        super()._done_moving(*args, **kwargs)
        self.xd.kill_cmd.put(1)
        self.xu.kill_cmd.put(1)

    def stop_and_kill(self):
        self.yu.stop()
        self.ydo.stop()
        self.ydi.stop()
        self.xu.stop()
        self.xd.stop()
        yield from mv(self.yu.kill_cmd,  1, self.ydo.kill_cmd,  1, self.ydi.kill_cmd,  1,
                      self.xu.kill_cmd,  1, self.xd.kill_cmd,  1)
        self.yu.clear_enc_lss.put(1)
        self.ydo.clear_enc_lss.put(1)
        self.ydi.clear_enc_lss.put(1)
        self.xu.clear_enc_lss.put(1)
        self.xd.clear_enc_lss.put(1)
        
    def kill_jacks(self):
        yield from mv(self.yu.kill_cmd,  1,
                      self.ydo.kill_cmd, 1,
                      self.ydi.kill_cmd, 1)

    def enable(self):
        yield from mv(self.xu.enable_cmd,  1,
                      self.xd.enable_cmd,  1,
                      self.yu.enable_cmd,  1,
                      self.ydo.enable_cmd, 1,
                      self.ydi.enable_cmd, 1)

    def ena(self):
        self.xu.enable_cmd.put(1)
        self.xd.enable_cmd.put(1)
        self.yu.enable_cmd.put(1)
        self.ydo.enable_cmd.put(1)
        self.ydi.enable_cmd.put(1)

    def where(self):
        if any(x.connected is False for x in (self.yu, self.ydi, self.ydo, self.xu, self.xd)):
            print(f'Some {self.name.capitalize()} motors are disconnected')
            print('Do check_for_synaxis() for more information.')
            return()
        stripe = ''
        if self.name.lower() == 'm3':
            if self.xu.user_readback.get() > 0:
                stripe = '(Rh/Pt stripe)'
            else:
                stripe = '(Si stripe)'
        #text += "%s: %s" % (self.name.upper(), stripe))
        text  = "      [white]vertical = %7.3f mm            YU  = %7.3f\n" % (self.vertical.readback.get(), self.yu.user_readback.get())
        text += "      lateral  = %7.3f mm            YDO = %7.3f\n" % (self.lateral.readback.get(),  self.ydo.user_readback.get())
        text += "      pitch    = %7.3f mrad          YDI = %7.3f\n" % (self.pitch.readback.get(),    self.ydi.user_readback.get())
        text += "      roll     = %7.3f mrad          XU  = %7.3f\n" % (self.roll.readback.get(),     self.xu.user_readback.get())
        text += "      yaw      = %7.3f mrad          XD  = %7.3f[/white]"   % (self.yaw.readback.get(),      self.xd.user_readback.get())
        #if self.name.lower() == 'm2':
        #    text += '\n      bender   = %9.1f steps' % m2_bender.user_readback.get()
        return text
    def wh(self):
        if any(x.connected is False for x in (self.yu, self.ydi, self.ydo, self.xu, self.xd)):
            print(f'Some {self.name.capitalize()} motors are disconnected')
            print('Do check_for_synaxis() for more information.')
            return()
        stripe = ''
        if self.name.lower() == 'm3':
            if self.xu.user_readback.get() > 0:
                stripe = ' (Rh/Pt stripe)'
            else:
                stripe = ' (Si stripe)'
        boxedtext(self.where(), title=f'{self.name} {stripe}', color='green')

    # The pseudo positioner axes:
    vertical = Cpt(PseudoSingle, limits=(-8, 8))
    lateral  = Cpt(PseudoSingle, limits=(-16, 16))
    pitch    = Cpt(PseudoSingle, limits=(-5.5, 5.5))
    roll     = Cpt(PseudoSingle, limits=(-3, 3))
    yaw      = Cpt(PseudoSingle, limits=(-3, 3))


    # The real (or physical) positioners:
    yu  = Cpt(XAFSEpicsMotor, 'YU}Mtr')
    ydo = Cpt(XAFSEpicsMotor, 'YDO}Mtr')
    ydi = Cpt(XAFSEpicsMotor, 'YDI}Mtr')
    xu  = Cpt(VacuumEpicsMotor, 'XU}Mtr')
    xd  = Cpt(VacuumEpicsMotor, 'XD}Mtr')

    @pseudo_position_argument
    def forward(self, pseudo_pos):
        '''Run a forward (pseudo -> real) calculation'''
        return self.RealPosition(xu  = pseudo_pos.lateral  - 0.5 * self.mirror_length * tan(pseudo_pos.yaw   / 1000),
                                 xd  = pseudo_pos.lateral  + 0.5 * self.mirror_length * tan(pseudo_pos.yaw   / 1000),

                                 yu  = pseudo_pos.vertical - 0.5 * self.mirror_length * tan(pseudo_pos.pitch / 1000),
                                 ydo = pseudo_pos.vertical + 0.5 * self.mirror_length * tan(pseudo_pos.pitch / 1000) + 0.5 * self.mirror_width * tan(pseudo_pos.roll/1000),
                                 ydi = pseudo_pos.vertical + 0.5 * self.mirror_length * tan(pseudo_pos.pitch / 1000) - 0.5 * self.mirror_width * tan(pseudo_pos.roll/1000)
                                 )

    @real_position_argument
    def inverse(self, real_pos):
        '''Run an inverse (real -> pseudo) calculation'''
        return self.PseudoPosition(lateral  = (real_pos.xu + real_pos.xd) / 2,
                                   yaw      = 1000*arctan2( real_pos.xd  - real_pos.xu,                    self.mirror_length),

                                   vertical = (real_pos.yu + (real_pos.ydo + real_pos.ydi) / 2 ) / 2,
                                   pitch    = 1000*arctan2( (real_pos.ydo + real_pos.ydi)/2 - real_pos.yu, self.mirror_length),
                                   roll     = 1000*arctan2( real_pos.ydo - real_pos.ydi,                   self.mirror_width ))



class XAFSTable(PseudoPositioner):
    def __init__(self, *args, mirror_length, mirror_width, **kwargs):
        self.mirror_length = mirror_length
        self.mirror_width  = mirror_width
        super().__init__(*args, **kwargs)

    def where(self):
        #text += "%s:" % self.name.upper())
        text  = "      [white]vertical = %7.3f mm            YU  = %7.3f\n" % (self.vertical.readback.get(), self.yu.user_readback.get())
        text += "      pitch    = %7.3f mrad          YDO = %7.3f\n" % (self.pitch.readback.get(),    self.ydo.user_readback.get())
        text += "      roll     = %7.3f mrad          YDI = %7.3f[/white]"   % (self.roll.readback.get(),     self.ydi.user_readback.get())
        return text
    def wh(self):
        boxedtext(self.where(), title='XAFS Table', color='green')

    # The pseudo positioner axes:
    vertical = Cpt(PseudoSingle, limits=(5, 145))
    pitch    = Cpt(PseudoSingle, limits=(-8, 6))
    roll     = Cpt(PseudoSingle, limits=(5, 5))


    # The real (or physical) positioners:
    yu  = Cpt(EpicsMotor, 'YU}Mtr')
    ydo = Cpt(EpicsMotor, 'YDO}Mtr')
    ydi = Cpt(EpicsMotor, 'YDI}Mtr')

    @pseudo_position_argument
    def forward(self, pseudo_pos):
        '''Run a forward (pseudo -> real) calculation'''
        return self.RealPosition(yu  = pseudo_pos.vertical - 0.5 * self.mirror_length * tan(pseudo_pos.pitch / 1000),
                                 ydo = pseudo_pos.vertical + 0.5 * self.mirror_length * tan(pseudo_pos.pitch / 1000) + 0.5 * self.mirror_width * tan(pseudo_pos.roll/1000),
                                 ydi = pseudo_pos.vertical + 0.5 * self.mirror_length * tan(pseudo_pos.pitch / 1000) - 0.5 * self.mirror_width * tan(pseudo_pos.roll/1000)
                                 )

    @real_position_argument
    def inverse(self, real_pos):
        '''Run an inverse (real -> pseudo) calculation'''
        return self.PseudoPosition(vertical = (real_pos.yu + (real_pos.ydo + real_pos.ydi) / 2 ) / 2,
                                   pitch    = 1000*arctan2( (real_pos.ydo + real_pos.ydi)/2 - real_pos.yu, self.mirror_length),
                                   roll     = 1000*arctan2( real_pos.ydo - real_pos.ydi,                   self.mirror_width ))






class GonioTable(PseudoPositioner):
    def __init__(self, *args, mirror_length, mirror_width, **kwargs):
        self.mirror_length = mirror_length
        self.mirror_width  = mirror_width
        super().__init__(*args, **kwargs)

    def where(self):
        #text += "%s:" % self.name.upper())
        text  = "      [white]vertical = %7.3f mm            YUO = %7.3f\n" % (self.vertical.readback.get(), self.yuo.user_readback.get())
        text += "      pitch    = %7.3f mrad          YUI = %7.3f\n" % (self.pitch.readback.get(),    self.yui.user_readback.get())
        text += "      roll     = %7.3f mrad          YD  = %7.3f[/white]"   % (self.roll.readback.get(),     self.yd.user_readback.get())
        return text
    def wh(self):
        boxedtext(text, title='Goniometer Table', color='green')

    # The pseudo positioner axes:
    vertical = Cpt(PseudoSingle, limits=(291, 412))
    pitch    = Cpt(PseudoSingle, limits=(-8, 1))
    roll     = Cpt(PseudoSingle, limits=(5, 5))


    # The real (or physical) positioners:
    yui = Cpt(EpicsMotor, 'YUI}Mtr')
    yuo = Cpt(EpicsMotor, 'YUO}Mtr')
    yd  = Cpt(EpicsMotor, 'YD}Mtr')

    @pseudo_position_argument
    def forward(self, pseudo_pos):
        '''Run a forward (pseudo -> real) calculation'''
        return self.RealPosition(yd  = pseudo_pos.vertical + 0.5 * self.mirror_length * tan(pseudo_pos.pitch / 1000),
                                 yuo = pseudo_pos.vertical - 0.5 * self.mirror_length * tan(pseudo_pos.pitch / 1000) + 0.5 * self.mirror_width * tan(pseudo_pos.roll/1000),
                                 yui = pseudo_pos.vertical - 0.5 * self.mirror_length * tan(pseudo_pos.pitch / 1000) - 0.5 * self.mirror_width * tan(pseudo_pos.roll/1000)
                                 )

    @real_position_argument
    def inverse(self, real_pos):
        '''Run an inverse (real -> pseudo) calculation'''
        return self.PseudoPosition(vertical = (real_pos.yd + (real_pos.yuo + real_pos.yui) / 2 ) / 2,
                                   pitch    = 1000*arctan2( real_pos.yd - (real_pos.yuo + real_pos.yui)/2, self.mirror_length),
                                   roll     = 1000*arctan2( real_pos.yuo - real_pos.yui,                   self.mirror_width ))

    
