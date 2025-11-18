from ophyd import (SingleTrigger, Component as Cpt, Device, DeviceStatus, EpicsSignal)
from bluesky.plan_stubs import null, sleep, mv, mvr
import time

from BMM.logging import report, BMM_msg_hook
from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

class EPS_Shutter(Device):
    state = Cpt(EpicsSignal, 'Pos-Sts')
    cls = Cpt(EpicsSignal, 'Cmd:Cls-Cmd')
    opn = Cpt(EpicsSignal, 'Cmd:Opn-Cmd')
    error = Cpt(EpicsSignal,'Err-Sts')
    permit = Cpt(EpicsSignal, 'Permit:Enbl-Sts')
    enabled = Cpt(EpicsSignal, 'Enbl-Sts')
    maxcount = 4
    openval = 1                 # normal shutter values, FS1 is reversed
    closeval = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        #self.color = 'red'

    def status(self):
        if self.state.get() == 1:
            return 'closed'
        else:
            return 'open'

    def open_plan(self):
        user_ns['RE'].msg_hook = None
        count = 0
        while self.state.get() != self.openval:
            count += 1
            print(u'\u231b', end=' ', flush=True)
            yield from mv(self.opn, 1)
            if count >= self.maxcount:
                print('tried %d times and failed to open %s %s' % (count, self.name, ':('))  # u'\u2639'  unicode frown
                return(yield from null())
            time.sleep(1.5)
        report('Opened {}'.format(self.name))
        user_ns['RE'].msg_hook = BMM_msg_hook

    def close_plan(self):
        user_ns['RE'].msg_hook = None
        count = 0
        while self.state.get() != self.closeval:
            count += 1
            print(u'\u231b', end=' ', flush=True)
            yield from mv(self.cls, 1)
            if count >= self.maxcount:
                print('tried %d times and failed to close %s %s' % (count, self.name, ':('))
                return(yield from null())
            time.sleep(1.5)
        report('Closed {}'.format(self.name))
        user_ns['RE'].msg_hook = BMM_msg_hook

    def open(self):
        user_ns['RE'].msg_hook = None
        if self.state.get() != self.openval:
            count = 0
            while self.state.get() != self.openval:
                count += 1
                print(u'\u231b', end=' ', flush=True)
                self.opn.put(1)
                if count >= self.maxcount:
                    print('tried %d times and failed to open %s %s' % (count, self.name, ':('))
                    return
                time.sleep(1.5)
            report(' Opened {}'.format(self.name))
        else:
            print('{} is open'.format(self.name))
        user_ns['RE'].msg_hook = BMM_msg_hook

    def close(self):
        user_ns['RE'].msg_hook = None
        if self.state.get() != self.closeval:
            count = 0
            while self.state.get() != self.closeval:
                count += 1
                print(u'\u231b', end=' ', flush=True)
                self.cls.put(1)
                if count >= self.maxcount:
                    print('tried %d times and failed to close %s %s' % (count, self.name, ':('))
                    return
                time.sleep(1.5)
            report(' Closed {}'.format(self.name))
        else:
            print('{} is closed'.format(self.name))
        user_ns['RE'].msg_hook = BMM_msg_hook

    def _state(self):
        if self.state.get() == self.closeval:
            state = 'closed'
            if self.name == 'FS1':
                return 'retracted'
            if self.name == 'LN2':
                return state
            return f'[red]{state}[/red]'
        state = 'open'
        if self.name == 'FS1':
            return '[red]in place[/red]'
        if self.name == 'LN2':
            return f'[pale_turquoise1]{state}[/pale_turquoise1]'
        return(state + '  ')

    
class BMPS_Shutter(Device):
    state = Cpt(EpicsSignal, 'Sts:BM_BMPS_Opn-Sts')

class IDPS_Shutter(Device):
    state = Cpt(EpicsSignal, 'Sts:BM_PS_OpnA3-Sts')


    

# single spinner is no longer in user
# class Spinner(Device):
#     state = Cpt(EpicsSignal, 'On_Off')
#
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#
#     def on(self):
#         report('Turning {} on'.format(self.name))
#         self.state.put(1)
#     start = on
#
#     def off(self):
#         report('Turning {} off'.format(self.name))
#         self.state.put(0)
#     stop = off
#
#     def on_plan(self):
#         report('Turning {} off'.format(self.name))
#         yield from mv(self.state, 1)
#
#     def off_plan(self):
#         report('Turning {} off'.format(self.name))
#         yield from mv(self.state, 0)
# fan = Spinner('XF:06BM-EPS{Fan}', name = 'spinner')

