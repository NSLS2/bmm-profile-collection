
from bluesky.plan_stubs import null, sleep, mv, mvr
from ophyd import Component as Cpt, EpicsSignal, EpicsSignalRO, Signal, Device, PVPositioner
from ophyd.signal import DerivedSignal
from ophyd.areadetector.base import EpicsSignalWithRBV
from ophyd.sim import FakeEpicsSignal

from BMMCommon.tools.messages import *  # error_msg et al. + boxedtext

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)


lssp = EpicsSignalRO('XF:06BM-BI{LS:331-1}:SP_RBV', name='LakeShore_setpoint')
lakeshore_done_flag = FakeEpicsSignal('XF:06BM-BI{LS:331-1}:fake', name='fake')


class LSatSetPoint(DerivedSignal):
    '''A signal that does a simple check of readback vs. setpoint'''
    def __init__(self, trigger, *, parent=None, **kwargs):
        trigger = getattr(parent, trigger)
        super().__init__(derived_from=trigger, parent=parent, **kwargs)

    def inverse(self, value):
        '''Very crude done condition.  As soon as the readback temperature
        exceeds the setpoint, return done and set a flag indicating
        done-ness. The point of the flag is so that, if the PID loop
        drops the temperature back below the setpoint, we want this to
        still signal done-ness.

        '''
        if lakeshore_done_flag.get() == 1:
            return 1
        if value > lssp.get():
            lakeshore_done_flag.put(1)
            return 1
        else:
            return 0

    def forward(self, value):
        return value


class LakeShore(PVPositioner):
    '''An ophyd wrapper around the LakeShore 331 controller.

    At BMM, communication to the LakeShore is through Moxa 7
    (xf06bm-tsrv7, 10.68.42.77) where port 2 is connected to the RS232
    port on the LakeShore.
    '''

    ## following https://blueskyproject.io/ophyd/positioners.html#pvpositioner
    readback = Cpt(EpicsSignalRO, 'CONTROL')
    setpoint = Cpt(EpicsSignalWithRBV, 'SP')
    done = Cpt(LSatSetPoint, trigger='readback')


    
    # PID signals (GAIN, RESET, RATE)
    p = Cpt(EpicsSignalWithRBV, 'P')
    i = Cpt(EpicsSignalWithRBV, 'I')
    d = Cpt(EpicsSignalWithRBV, 'D')

    # Ramp Rate (K/min)
    ramp_rate = Cpt(EpicsSignalWithRBV, 'RAMP_RATE')

    # Ramp Enable/Disable
    ramp = Cpt(EpicsSignalWithRBV, 'RAMP')

    # Heater Range
    power = Cpt(EpicsSignalWithRBV, 'HEAT_RNG')

    # Input and Units Selectors
    input_sel = Cpt(EpicsSignal, 'INPUT_SEL')
    units_sel = Cpt(EpicsSignal, 'UNITS_SEL')

    # Channel A/B Temps
    sample_a = Cpt(EpicsSignalRO, 'SAMPLE_A')
    sample_b = Cpt(EpicsSignalRO, 'SAMPLE_B')


    # Heater Power (%)
    heater_pwr = Cpt(EpicsSignalRO, 'HEATER_PWR')

    # Scanrates
    temp_scan_rate = Cpt(EpicsSignal, 'READ.SCAN')
    ctrl_scan_rate = Cpt(EpicsSignal, 'READ_RDAT_SCALC.SCAN')


    # Serial Connection (for debugging)
    serial = Cpt(EpicsSignal, 'SERIAL')

    # a sort of temperature deadband, don't even try to change
    # temperature if already within this amount.
    deadband = 3.0
    
    
    # Utility signals/PVs
    # read_pid = Cpt(EpicsSignal, 'READ_PID')
    # read_read = Cpt(EpicsSignal, 'READ')
    # read_p = Cpt(EpicsSignal, 'READ_P')
    # read_i = Cpt(EpicsSignal, 'READ_I')
    # read_d = Cpt(EpicsSignal, 'READ_D')
    # read_ramp = Cpt(EpicsSignal, 'READ_RAMP')
    # read_sp_str = Cpt(EpicsSignal, 'READ_SP_STR')
    # write_read_p = Cpt(EpicsSignal, 'WRITE_READ_P')
    # write_read_i = Cpt(EpicsSignal, 'WRITE_READ_I')
    # write_read_d = Cpt(EpicsSignal, 'WRITE_READ_D')
    # read_ctrl = Cpt(EpicsSignal, 'READ_CTRL')
    # read_ctrl_params = Cpt(EpicsSignal, 'READ_CTRL_PARAMS')
    # read_heater_pwr = Cpt(EpicsSignal, 'READ_HEATER_PWR')
    # write_sp = Cpt(EpicsSignal, 'WRITE_SP')
    # read_sp = Cpt(EpicsSignal, 'READ_SP')
    # read_sample_a = Cpt(EpicsSignal, 'READ_SAMPLE_A')
    # read_sample_b = Cpt(EpicsSignal, 'READ_SAMPLE_B')
    # write_read_htr = Cpt(EpicsSignal, 'WRITE_READ_HTR')
    # write_read_ramp = Cpt(EpicsSignal, 'WRITE_READ_RAMP')
    # set_p = Cpt(EpicsSignal, 'SET_P')
    # set_i = Cpt(EpicsSignal, 'SET_I')
    # set_d = Cpt(EpicsSignal, 'SET_D')
    # ramp_scalc = Cpt(EpicsSignal, 'RAMP_SCALC')
    # htr_range = Cpt(EpicsSignal, 'RANGE')
    # sp_scalc = Cpt(EpicsSignal, 'SP_SCALC')
    # ctrl_input = Cpt(EpicsSignal, 'CTRL_INPUT')
    # ctrl_units = Cpt(EpicsSignal, 'CTRL_UNITS')
    # ctrl_units_str = Cpt(EpicsSignal, 'CTRL_UNITS_STR')
    # set_ctrl = Cpt(EpicsSignal, 'SET_CTRL')
    # read_ctrl_scalc = Cpt(EpicsSignal, 'READ_CTRL_SCALC')
    # set_heat = Cpt(EpicsSignal, 'SET_HEAT')
    # read_spla_scalc = Cpt(EpicsSignal, 'READ_SPLA_SCALC')
    # read_splb_scalc = Cpt(EpicsSignal, 'READ_SPLB_SCALC')
    # read_rdat_scalc = Cpt(EpicsSignal, 'READ_RDAT_SCALC')
    # set_ramp = Cpt(EpicsSignal, 'SET_RAMP')

    def level(self, value):
        '''Return the proper integer (0 - 3) for the requested power level:
        1 = "low" or "100 mA"
        2 = "medium" or "300 mA"
        3 = "high" or "1 A"
        
        Any other string will resolve to 0, which is off.

        Any integer greater than 3 will resolve to 3.

        Any integer less than 0 will resolve to 0.
        '''
        if type(value) is int:
            if value > 3:
                value = 3
            if value < 0:
                value = 0
        if type(value) is str:
            if value.lower() == 'low' or value.lower() == '100 mA':
                value = 1
            elif value.lower() == 'medium' or value.lower() == '300 mA':
                value = 2
            elif value.lower() == 'high' or value.lower() == '1 A':
                value = 3
            else:
                value = 0
        return(value)

    def on(self, heater='medium'):
        self.power.put(self.level(heater))

    def off(self):
        self.power.put(0)
        lakeshore_done_flag.put(0)
                

    def on_plan(self, heater='medium'):
        yield from mv(self.power, self.level(heater))

    def off_plan(self):
        yield from mv(self.power, 0)
        yield from mv(lakeshore_done_flag, 0)


    def to(self, target, heater='medium'):
        '''Set the power level and setpoint, then begin a move to the
        setpoint.

        '''
        if abs(self.readback.get() - target) < self.deadband:
            warning_msg(f'Requested temperature target is within the temperature deadband, {self.deadband}K')
            warning_msg('Not attempting to change temperature.')
            yield from mv(user_ns['busy'], 10)
        else:
            yield from mv(self.setpoint, target)
            yield from mv(self.power, self.level(heater))
            rate = self.ramp_rate.get()
            if rate == 0:
                rate = 1
            deltat = int(1.3 * (abs(self.setpoint.get() - self.readback.get()) / rate)) * 60
            #yield from mv(self, target)
            print(f'Waiting {deltat/60.0:.1f} minutes to arrive at temperature.')
            yield from mv(user_ns['busy'], deltat)
        

    def units(self, unit):
        if unit.lower()[0] == 'c':
            self.units_sel.put('C')
            return
        if unit.lower()[0] == 'k':
            self.units_sel.put('K')
            return
        warning_msg('LakeShore 331 units not changed.  Valid units are Kelvin and Celsius (as strings)."')


    def status(self):
        text  = f'\nControl temperature = {self.readback.get():.1f}, setpoint = {self.setpoint.get():.1f}\n\n'
        controlA, controlB = f'(control)', ''
        if self.input_sel.get() == 'B':
            controlA, controlB = '', f'(control)'
        text += f'Sample temperature A = {self.sample_a.get()} {controlA}\n'
        text += f'Sample temperature B = {self.sample_b.get()} {controlB}\n\n'
        text += f'Power = {self.heater_pwr.get()}%   Range = {("Off", "100 mA", "300 mA", "1 A")[self.power.get()]}\n\n'
        text += f'Settling time: {self.settle_time} seconds\n\n'
        yesno = 'yes' if lakeshore_done_flag.get() == 1 else 'no'
        text += f'Resting at setpoint: {yesno}\n\n'
        text += f'(scan rate = {self.temp_scan_rate.describe()["LakeShore 331_temp_scan_rate"]["enum_strs"][self.temp_scan_rate.get()]})\n'
        
        boxedtext(text, title='Lakeshore 331', color='green')


    def dossier_entry(self):
        thistext  =  '	    <div>\n'
        thistext +=  '	      <h3>Instrument: Displex/LakeShore 331</h3>\n'
        thistext +=  '	      <ul>\n'
        thistext += f'               <li><b>Temperature sensor A:</b> {self.sample_a.get():.1f} {self.units_sel.enum_strs[self.units_sel.get()]}</li>\n'
        thistext += f'               <li><b>Temperature sensor B:</b> {self.sample_b.get():.1f} {self.units_sel.enum_strs[self.units_sel.get()]}</li>\n'
        thistext += f'               <li><b>Control sensor:</b> {self.input_sel.enum_strs[self.input_sel.get()]}</li>\n'
        thistext += f'               <li><b>Set point:</b> {self.setpoint.get():.1f} {self.units_sel.enum_strs[self.units_sel.get()]}</li>\n'
        thistext += f'               <li><b>Heater range:</b> {self.power.enum_strs[self.power.get()]}</li>\n'
        thistext += f'               <li><b>Heater power:</b> {self.heater_pwr.get():.1f}%</li>\n'
        thistext +=  '	      </ul>\n'
        thistext +=  '	    </div>\n'
        return thistext

        

from BMM.macrobuilder import BMMMacroBuilder

class LakeShoreMacroBuilder(BMMMacroBuilder):
    '''A class for parsing specially constructed spreadsheets and
    generating macros for measuring XAS using the Linkam stage.

    Examples
    --------
    >>> lmb = LinkamMacroBuilder()
    >>> lmb.spreadsheet('linkam.xlsx')
    >>> lmb.write_macro()

    '''
    macro_type = 'LakeShore'
        
    def _write_macro(self):
        '''Write a macro paragraph for each sample described in the
        spreadsheet.  A paragraph consists of lines to change the
        temperature, lines to move sample and detector to the correct
        positions (if needed), a line to change the edge energy (if
        needed), a line to measure the XAFS using the correct set of
        control parameters, and a line to close plot windows after the
        scan.

        '''
        element, edge, focus = (None, None, None)
        settle_time, ramp_time = 0,0
        previous = 25
        self.tab = ' '*8
        count = 0
        
        self.content = ""
        self.content += self.check_edge()

        self.content += self.tab + 'yield from mv(lakeshore.setpoint, lakeshore.readback.get())\n\n'

        for m in self.measurements:

            if m['default'] is True:
                element     = m['element']
                edge        = m['edge']
                temperature = m['temperature']
                continue
            if m['temperature'] is None:
                continue
            if self.skip_row(m) is True:
                continue

            count += 1
            self.content += self.tab + f'report("{self.macro_type} sequence {count} of {self.calls_to_xafs}", level="bold", slack=True)\n'

            #######################################
            # default element/edge(/focus) values #
            #######################################
            for k in ('element', 'edge', 'focus', 'power'):
                if m[k] is None:
                    m[k] = self.measurements[0][k]
            if m['settle'] == 0:
                m['settle'] = self.measurements[0]['settle']

            ################################
            # change temperature is needed #
            ################################
            #self.content += self.tab + f'lakeshore.settle_time = {m["settle"]:.1f}\n'
            if m['temperature'] != temperature:
                if self.check_temp(user_ns['lakeshore'], m['temperature']) is False: return(False)
                self.content += self.tab + f"report('== Moving to temperature {m['temperature']:.1f}C', slack=True)\n"
                self.content += self.tab + f'yield from lakeshore.to({m["temperature"]:.1f}, heater=\'{m["power"]}\')\n'
                self.content += self.tab + f'yield from mv(busy, {m["settle"]:.1f})\n'
                temperature = m['temperature']
                settle_time += m["settle"]
                rate = user_ns['lakeshore'].ramp_rate.get()
                if rate == 0:
                    rate = 1
                ramp_time += (temperature - previous) / rate
                previous = temperature

            ############################
            # sample and slit movement #
            ############################           
            if m['samplex'] is not None:
                if self.check_limit(user_ns['xafs_x'], m['samplex']) is False: return(False)
                self.content += self.tab + f'yield from mv(xafs_x, {m["samplex"]:.3f})\n'
            if m['sampley'] is not None:
                if self.check_limit(user_ns['xafs_y'], m['sampley']) is False: return(False)
                self.content += self.tab + f'yield from mv(xafs_y, {m["sampley"]:.3f})\n'
            if m['detectorx'] is not None:
                if self.check_limit(user_ns['xafs_detx'], m['detectorx']) is False: return(False)
                self.content += self.tab + f'yield from mv(xafs_detx, {m["detectorx"]:.2f})\n'

            if m['slitwidth'] is not None:
                if self.check_limit(user_ns['slits3'].hsize, m['slitwidth']) is False: return(False)
                self.content += self.tab + 'yield from mv(slits3.hsize, %.2f)\n' % m['slitwidth']
            if m['slitheight'] is not None:
                if self.check_limit(user_ns['slits3'].vsize, m['slitheight']) is False: return(False)
                self.content += self.tab + 'yield from mv(slits3.vsize, %.2f)\n' % m['slitheight']
                
            
            ##########################
            # change edge, if needed #
            ##########################
            focus = False
            if m['focus'] == 'focused':
                focus = True
            text, time, inrange = self.do_change_edge(m['element'], m['edge'], focus, self.tab)
            if inrange is False: return(False)
            
            # if self.do_first_change is True:
            #     self.do_first_change = False
            #     self.content += text
            #     self.totaltime += time
                
            elif m['element'] != element or m['edge'] != edge: # focus...
                element = m['element']
                edge    = m['edge']
                self.content += text
                self.totaltime += time
                
            else:
                if self.verbose:
                    self.content += self.tab + '## staying at %s %s\n' % (m['element'], m['edge'])
                pass

            ######################################
            # measure XAFS, then close all plots #
            ######################################
            command = self.tab + 'yield from xafs(\'%s.ini\'' % self.basename
            for k in m.keys():
                ## skip cells with macro-building parameters that are not INI parameters
                if self.skip_keyword(k):
                    continue
                ## skip element & edge if they are same as default
                elif k in ('element', 'edge'):
                    if m[k] == self.measurements[0][k]:
                        continue
                ## skip cells with only whitespace
                if type(m[k]) is str and len(m[k].strip()) == 0:
                    m[k] = None
                ## if a cell has data, put it in the argument list for xafs()
                if m[k] is not None:
                    if k == 'filename':
                        fname = self.make_filename(m)
                        command += f', filename=\'{fname}\''
                    elif type(m[k]) is int:
                        command += ', %s=%d' % (k, m[k])
                    elif type(m[k]) is float:
                        command += ', %s=%.3f' % (k, m[k])
                    else:
                        command += ', %s=\'%s\'' % (k, m[k])
            command += ', copy=False)\n'
            self.content += command
            self.content += self.tab + 'close_plots()\n\n'
            #self.content += self.tab + 'yield from lakeshore.off_plan()\n\n'

            ########################################
            # approximate time cost of this sample #
            ########################################
            self.estimate_time(m, element, edge)
            

        whisper(f'XAS time:      about {self.totaltime/60:.1f} hours')
        whisper(f'ramp time:     about {ramp_time:.1f} minutes (this estimate is probably unreliable)')
        whisper(f'settling time: about {settle_time/60:.1f} minutes')
        self.totaltime = self.totaltime + (settle_time / 60) + ramp_time
        if self.close_shutters:
            self.content += self.tab + 'if not dryrun:\n'
            self.content += self.tab + '    BMMuser.running_macro = False\n'
            self.content += self.tab + '    suspenders.clear_suspenders()\n'
            self.content += self.tab + '    yield from shb.close_plan()\n'
        return(True)

    def get_keywords(self, row, defaultline):
        '''Instructions for parsing spreadsheet columns into keywords.

        arguments
        ---------
        row : contents of a row as read by openpyxl, i.e. ws.rows
        defaultline : True only if this row contains the default
        parameters, i.e. the green row

        This must return a dictionary.  The dictionary keys are the
        keywords related to the column labels from the spreadsheet,
        the values are cell contents, possibly coerced to a specific
        type.

        '''
        this = {'default':     defaultline,
                'temperature': row[1].value,          # measurement temperature
                'settle':      self.nonezero(row[2].value),  # temperature settling time
                'power':       row[3].value,                 # heater power level
                'measure':     self.truefalse(row[4].value, 'measure'), # filename and visualization
                'filename':    str(row[5].value),
                'nscans':      row[6].value,
                'start':       row[7].value,
                'mode':        row[8].value,
                'element':     row[9].value,      # energy range
                'edge':        row[10].value,
                'focus':       row[11].value,
                'sample':      self.escape_quotes(str(row[12].value)),     # scan metadata
                'prep':        self.escape_quotes(str(row[13].value)),
                'comment':     self.escape_quotes(str(row[14].value)),
                'bounds':      row[15].value,     # scan parameters
                'steps':       row[16].value,
                'times':       row[17].value,
                'detectorx':   row[18].value,
                'samplex':     row[19].value,     # other motors
                'sampley':     row[20].value,
                'slitwidth':   row[21].value,
                'slitheight':  row[22].value,
                'snapshots':   self.truefalse(row[23].value, 'snapshots' ),  # flags
                'htmlpage':    self.truefalse(row[24].value, 'htmlpage'  ),
                'usbstick':    self.truefalse(row[25].value, 'usbstick'  ),
                'bothways':    self.truefalse(row[26].value, 'bothways'  ),
                'channelcut':  self.truefalse(row[27].value, 'channelcut'),
                'ththth':      self.truefalse(row[28].value, 'ththth'    ),
                'url':         row[29].value,
                'doi':         row[30].value,
                'cif':         row[31].value, }
        return this

        
