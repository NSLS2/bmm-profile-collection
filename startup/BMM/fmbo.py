import inspect, time
from ophyd import EpicsSignalRO
from BMMCommon.tools.messages import *  # error_msg et al. + boxedtext

def is_FMBO_motor(motor):
    inheritance = (str(x) for x in inspect.getmro(motor.__class__))
    for cl in inheritance:
        if "FMBOEpicsMotor" in cl:
            return True
    return False

# expected values of signals
status_list = {'mtact' : 1, 'mlim'  : 0, 'plim'  : 0, 'ampen' : 0,
               'loopm' : 1, 'tiact' : 0, 'intmo' : 1, 'dwpro' : 0,
               'daerr' : 0, 'dvzer' : 0, 'abdec' : 0, 'uwpen' : 0,
               'uwsen' : 0, 'errtg' : 0, 'swpoc' : 0, 'asscs' : 1,
               'frpos' : 0, 'hsrch' : 0, 'sodpl' : 0, 'sopl'  : 0,
               'hocpl' : 1, 'phsra' : 0, 'prefe' : 0, 'trmov' : 0,
               'iffe'  : 0, 'amfae' : 0, 'amfe'  : 0, 'fafoe' : 0,
               'wfoer' : 0, 'inpos' : 1, 'enc_lss' : 0}

def FMBO_status(motor):
    '''Inspect signals from FMBO motors using disposable EpicsSignal objects.
    '''
    if is_FMBO_motor(motor) is False:
        print(f'{motor.name} is not an FMBO motor. The CSS screen should be complete for this axis.')
        return
    else:
        text = f'\n  {motor.name} is {motor.prefix}\n\n'
        for a in status_list.keys():
            desc   = EpicsSignalRO(f'{motor.prefix}_{a.upper()}_STS.DESC', name='')
            signal = EpicsSignalRO(f'{motor.prefix}_{a.upper()}_STS',      name=desc.get())
            count  = 0
            while signal.connected is False:
                time.sleep(0.05)
                count += 1
                if count > 5:
                    continue
            suffix = f'_{a.upper()}_STS'
            padding = ' ' * (12-len(suffix))
            string = signal.enum_strs[signal.get()]
            if a != 'asscs':
                if signal.get() != status_list[a]:
                    string = warning_msg(f'{string: <19}')
            text += f'   {desc.get():26}  {string: <19}  {bold_msg(signal.get())}   {whisper(suffix)}{padding}\n'
        boxedtext(text, title=f'{motor.name} status signals', color='yellow')

