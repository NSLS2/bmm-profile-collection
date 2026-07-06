from ophyd import QuadEM, Component as Cpt, EpicsSignalWithRBV, Signal, DerivedSignal, EpicsSignal
from ophyd.quadem import QuadEMPort

from numpy import log, exp
from bluesky.plan_stubs import mv, sleep

from BMM.logging import BMM_log_info

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

#_locked_dwell_time = user_ns['_locked_dwell_time']
from BMM.user_ns.dwelltime   import _locked_dwell_time, with_quadem, with_ic0, with_ic1, with_ic2
from BMM.user_ns.instruments import shb



class Nanoize(DerivedSignal):
    def forward(self, value):
        return value * 1e-9 / _locked_dwell_time.dwell_time.readback.get()
    def inverse(self, value):
        return value * 1e9 * _locked_dwell_time.dwell_time.readback.get()



class BMMQuadEM(QuadEM):
    _default_read_attrs = ['I0',
                           'It',
                           'Ir',
                           'Iy']
    port_name = Cpt(Signal, value='EM180')
    conf = Cpt(QuadEMPort, port_name='EM180')
    em_range  = Cpt(EpicsSignalWithRBV, 'Range', string=True)
    I0 = Cpt(Nanoize, derived_from='current1.mean_value')
    It = Cpt(Nanoize, derived_from='current2.mean_value')
    Ir = Cpt(Nanoize, derived_from='current4.mean_value')  # !!FIXME!!
    Iy = Cpt(Nanoize, derived_from='current3.mean_value')  # !!FIXME!!

    compute_current_offset1 = Cpt(EpicsSignal, 'ComputeCurrentOffset1.PROC')
    compute_current_offset2 = Cpt(EpicsSignal, 'ComputeCurrentOffset2.PROC')
    compute_current_offset3 = Cpt(EpicsSignal, 'ComputeCurrentOffset4.PROC')  # !!FIXME!!
    compute_current_offset4 = Cpt(EpicsSignal, 'ComputeCurrentOffset3.PROC')  # !!FIXME!!
    
    
    #state  = Cpt(EpicsSignal, 'Acquire')

    # XF:06BM-BI{EM:1}EM180:Current1:EnableCallbacks
    # XF:06BM-BI{EM:1}EM180:Current2:EnableCallbacks
    # XF:06BM-BI{EM:1}EM180:Current3:EnableCallbacks
    # XF:06BM-BI{EM:1}EM180:Current4:EnableCallbacks
    # XF:06BM-BI{EM:1}EM180:SumX:EnableCallbacks
    # XF:06BM-BI{EM:1}EM180:SumY:EnableCallbacks
    # XF:06BM-BI{EM:1}EM180:SumAll:EnableCallbacks
    # XF:06BM-BI{EM:1}EM180:DiffX:EnableCallbacks
    # XF:06BM-BI{EM:1}EM180:DiffY:EnableCallbacks
    # XF:06BM-BI{EM:1}EM180:PosX:EnableCallbacks
    # XF:06BM-BI{EM:1}EM180:PosY:EnableCallbacks
    # XF:06BM-BI{EM:1}EM180:image1:EnableCallbacks
    # XF:06BM-BI{EM:1}EM180:netCDF1:EnableCallbacks
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._acquisition_signal = self.acquire
        self.configuration_attrs = ['integration_time', 'averaging_time','em_range','num_averaged','values_per_read']

    def _acquire_changed(self, value=None, old_value=None, **kwargs):
        super()._acquire_changed(value=value, old_value=old_value, **kwargs)
        status = self._status
        if status is not None and status.done:
            # Clear the state to be ready for the next round.
            self._status = None
        
    def on(self, quiet=False):
        if quiet is False:
            print('Turning {} on'.format(self.name))
        self.acquire_mode.put(0)
        self.acquire.put(1)

    def off(self, quiet=False):
        if quiet is False:
            print('Turning {} off'.format(self.name))
        self.acquire_mode.put(2)
        self.acquire.put(0)

    def on_plan(self):
        yield from mv(self.acquire, 1)
        yield from mv(self.acquire_mode, 0)

    def off_plan(self):
        yield from mv(self.acquire, 0)
        yield from mv(self.acquire_mode, 2)

    def enable_electrometer(self):
        '''Set various quadEM parameters to the values used at BMM.  This is
        particularly useful after power cycling or some other
        disruptive event.  And it is harmless when stopping and
        restarting bsui under normal circumstances.

        '''
        stats_pvs = ('XF:06BM-BI{EM:1}EM180:Current1:EnableCallbacks',
                     'XF:06BM-BI{EM:1}EM180:Current2:EnableCallbacks',
                     'XF:06BM-BI{EM:1}EM180:Current3:EnableCallbacks',
                     'XF:06BM-BI{EM:1}EM180:Current4:EnableCallbacks',
                     'XF:06BM-BI{EM:1}EM180:SumX:EnableCallbacks',
                     'XF:06BM-BI{EM:1}EM180:SumY:EnableCallbacks',
                     'XF:06BM-BI{EM:1}EM180:SumAll:EnableCallbacks',
                     'XF:06BM-BI{EM:1}EM180:DiffX:EnableCallbacks',
                     'XF:06BM-BI{EM:1}EM180:DiffY:EnableCallbacks',
                     'XF:06BM-BI{EM:1}EM180:PosX:EnableCallbacks',
                     'XF:06BM-BI{EM:1}EM180:PosY:EnableCallbacks')
        for pv in stats_pvs:
            EpicsSignal(pv, name='').put(1)
        EpicsSignal('XF:06BM-BI{EM:1}EM180:Range', name='').put(7)
        EpicsSignal('XF:06BM-BI{EM:1}EM180:AveragingTime', name='').put(0.5)
        EpicsSignal('XF:06BM-BI{EM:1}EM180:Acquire', name='').put(1)

    def dark_current(self):
        print(f'Measuring current offsets for {self.name}, this may take several seconds')
        yield from mv(self.compute_current_offset1,1)
        yield from mv(self.compute_current_offset2,1)
        yield from mv(self.compute_current_offset3,1)
        yield from mv(self.compute_current_offset4,1)
        BMM_log_info('Measured dark current on quadem1')

    def get_current_offsets(self):
        return(self.current_offsets.ch1.get(), self.current_offsets.ch2.get(), self.current_offsets.ch3.get(), self.current_offsets.ch4.get())
        

class BMMDualEM(QuadEM):
    _default_read_attrs = ['Ia',
                           'Ib']
    port_name = Cpt(Signal, value='NSLS2_IC')
    conf = Cpt(QuadEMPort, port_name='NSLS2_IC')
    em_range = Cpt(EpicsSignalWithRBV, 'Range', string=True)
    Ia = Cpt(Nanoize, derived_from='current1.mean_value')
    Ib = Cpt(Nanoize, derived_from='current2.mean_value')
    state = Cpt(EpicsSignal, 'Acquire')

    calibration_mode = Cpt(EpicsSignal, 'CalibrationMode')
    copy_adc_offsets = Cpt(EpicsSignal, 'CopyADCOffsets.PROC')
    compute_current_offset1 = Cpt(EpicsSignal, 'ComputeCurrentOffset1.PROC')
    compute_current_offset2 = Cpt(EpicsSignal, 'ComputeCurrentOffset2.PROC')

    sigma1 = Cpt(EpicsSignal, 'Current1:Sigma_RBV')
    sigma2 = Cpt(EpicsSignal, 'Current1:Sigma_RBV')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        #for c in ['current{}'.format(j) for j in range(1, 5)]:
        #     getattr(self, c).read_attrs = ['mean_value']

        # self.read_attrs = ['current{}'.format(j) for j in range(1, 5)]
        self._acquisition_signal = self.acquire
        self.configuration_attrs = ['integration_time', 'averaging_time','em_range','num_averaged','values_per_read']

    def _acquire_changed(self, value=None, old_value=None, **kwargs):
        super()._acquire_changed(value=value, old_value=old_value, **kwargs)
        status = self._status
        if status is not None and status.done:
            # Clear the state to be ready for the next round.
            self._status = None
        
    def on(self):
        print('Turning {} on'.format(self.name))
        self.acquire_mode.put(0)
        self.acquire.put(1)

    def off(self):
        print('Turning {} off'.format(self.name))
        self.acquire_mode.put(2)
        self.acquire.put(0)

    def on_plan(self):
        yield from mv(self.acquire, 1)
        yield from mv(self.acquire_mode, 0)

    def off_plan(self):
        yield from mv(self.acquire, 0)
        yield from mv(self.acquire_mode, 2)

    def dark_current(self):
        reopen = shb.state.get() == shb.openval
        if reopen:
            print('\nClosing photon shutter')
            yield from shb.close_plan()
        print(f'Measuring current offsets on {self.name}, this will take several seconds')

        ######################################################################
        # from Pete (email Feb 7, 2020):                                     #
        #   caput XF:06BM-BI{EM:3}EM180:CalibrationMode 1                    #
        #   caput XF:06BM-BI{EM:3}EM180:CopyADCOffsets.PROC 1                #
        #   caput XF:06BM-BI{EM:3}EM180:CalibrationMode 0                    #
        # ADC offset values should be around 3800, might need to hit Compute #
        # buttons to get lovely low dark current values                      #
        ######################################################################

        ## this almost works....
        self.current_offsets.ch1.put(0.0)
        self.current_offsets.ch2.put(0.0)
        self.calibration_mode.put(1)
        yield from sleep(0.5)
        self.copy_adc_offsets.put(1)
        yield from sleep(0.5)
        self.calibration_mode.put(0)
        yield from sleep(0.5)
        self.compute_current_offset1.put(1)
        self.compute_current_offset2.put(1)
        # EpicsSignal("XF:06BM-BI{EM:3}EM180:CalibrationMode", name='').put(1)
        # EpicsSignal("XF:06BM-BI{EM:3}EM180:CopyADCOffsets.PROC", name='').put(1)
        # EpicsSignal("XF:06BM-BI{EM:3}EM180:CalibrationMode", name='').put(0)
        # EpicsSignal("XF:06BM-BI{EM:1}EM180:ComputeCurrentOffset1.PROC", name='').put(1)
        # EpicsSignal("XF:06BM-BI{EM:1}EM180:ComputeCurrentOffset2.PROC", name='').put(1)
        yield from sleep(0.5)
        print(self.sigma1.get(), self.sigma2.get())
        BMM_log_info('Measured dark current on dualio ion chamber')
        if reopen:
            print('Opening photon shutter')
            yield from shb.open_plan()
            print('You are ready to measure!\n')


class IntegratedIC(BMMDualEM):
    bias = Cpt(EpicsSignal, 'BiasVoltage')
    capacitor_range = Cpt(EpicsSignal, 'Range')

    def enable_electrometer(self):
        '''Set various quadEM parameters to the values used at BMM.  This is
        particularly useful after power cycling or some other
        disruptive event.  And it is harmless when stopping and
        restarting bsui under normal circumstances.

        '''
        stats_pvs = (self.prefix + 'Current1:EnableCallbacks',
                     self.prefix + 'Current2:EnableCallbacks',
                     self.prefix + 'Current3:EnableCallbacks',
                     self.prefix + 'Current4:EnableCallbacks',
                     self.prefix + 'SumX:EnableCallbacks',
                     self.prefix + 'SumY:EnableCallbacks',
                     self.prefix + 'SumAll:EnableCallbacks',
                     self.prefix + 'DiffX:EnableCallbacks',
                     self.prefix + 'DiffY:EnableCallbacks',
                     self.prefix + 'PosX:EnableCallbacks',
                     self.prefix + 'PosY:EnableCallbacks')
        for pv in stats_pvs:
            EpicsSignal(pv, name='').put(1)
        EpicsSignal(self.prefix + 'Range', name='').put(7)
        EpicsSignal(self.prefix + 'AveragingTime', name='').put(0.5)
        EpicsSignal(self.prefix + 'Acquire', name='').put(1)
        EpicsSignal(self.prefix + 'BiasVoltage', name='').put(200)

    def dark_current(self):
        print(f'Measuring current offsets for {self.name}, this may take several seconds')
        yield from mv(self.compute_current_offset1,1)
        yield from mv(self.compute_current_offset2,1)
        BMM_log_info(f'Measured dark current on {self.name}')

    def get_current_offsets(self):
        return(self.current_offsets.ch1.get(), self.current_offsets.ch2.get())

        
def dark_current():
    reopen = shb.state.get() == shb.openval 
    if reopen:
        print('\nClosing photon shutter')
        yield from user_ns['shb'].close_plan()
        yield from sleep(3)
    if with_quadem:
        yield from user_ns['quadem1'].dark_current()
    if with_ic0:
        yield from user_ns['ic0'].dark_current()
    if with_ic1:
        yield from user_ns['ic1'].dark_current()
    if with_ic2:
        yield from user_ns['ic2'].dark_current()
    if reopen:
        print('Opening photon shutter')
        yield from user_ns['shb'].open_plan()
        print('You are ready to measure!\n')
