try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False

from bluesky.plan_stubs import sleep, mv, mvr, null
from bluesky.preprocessors import finalize_wrapper
from ophyd import Component as Cpt, EpicsSignal, EpicsSignalRO, Signal, Device

import os, re, shutil, glob, psutil
from openpyxl import load_workbook
import configparser
import numpy
from pathlib import Path

from bmm_tools.tools.misc import now
from bmm_tools.tools.messages import *  # error_msg et al. + boxedtext
from bmm_tools.tools.animated_prompt import PROMPTNC, animated_prompt
from bmm_tools.tools.md import proposal_base

from BMM.functions         import present_options
from BMM.user_ns.bmm       import kafka
from BMM.logging           import BMM_log_info, BMM_msg_hook, report
from BMM.linescans         import linescan, prepare_alignment_scan, fetch_peak_position_via_redis
from BMM.macrobuilder      import BMMMacroBuilder
from BMM.modes             import get_mode
from BMM.resting_state     import resting_state_plan
from BMM.xafs_functions    import conventional_grid
from BMM.user_ns.dwelltime import _locked_dwell_time

from bmm_tools.tools.periodictable import PERIODIC_TABLE, edge_energy

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from BMM.user_ns.motors     import xafs_garot, xafs_pitch, xafs_x, xafs_y
from BMM.user_ns.suspenders import suspenders

class GlancingAngle(Device):
    '''A class capturing the movement and control of the glancing angle
    spinner stage.

    Attributes
    ==========
    spinner1 .. spinner8 : EpicsSignal
        The EpicsSignal component for spinner #1 .. 8
    spin : Bool
        When True, the ga.to() method will start the spinner
    home : float
        Position in xafs_garot of spinner 0
    inverted : str
        A string used to recognize whether the linear scan steps up or down
    flat : list
        The linear and pitch positions of the flat sample on the current spinner
    y_uid : str
        The DataBroker UID of the most recent linear against It scan
    pitch_uid : str
        The DataBroker UID of the most recent xafs_pitch against It scan
    f_uid : str
        The DataBroker UID of the most recent linear against fluorescence scan
    alignment_filename : str
        The fully resolved path to the three-panel, auto-alignment png image


    Methods
    =======
    current : return the current spoinner number as an integer
    reset : return xafs_garot to its home position
    on : turn on the specified spinner
    off : turn off the specified spinner
    alloff : turn off all spinners
    alloff_plan : turn off all spinners as a plan
    to : move to the specified spinner, turn off all other spinners, turn on this spinner
    auto_align : perform an automated alignment for the current spinner

    '''
    spinner1 = Cpt(EpicsSignal, 'OutPt08:Data-Sel')
    spinner2 = Cpt(EpicsSignal, 'OutPt09:Data-Sel')
    spinner3 = Cpt(EpicsSignal, 'OutPt10:Data-Sel')
    spinner4 = Cpt(EpicsSignal, 'OutPt11:Data-Sel')
    spinner5 = Cpt(EpicsSignal, 'OutPt12:Data-Sel')
    spinner6 = Cpt(EpicsSignal, 'OutPt13:Data-Sel')
    spinner7 = Cpt(EpicsSignal, 'OutPt14:Data-Sel')
    spinner8 = Cpt(EpicsSignal, 'OutPt15:Data-Sel')
    #rotation

    spin = True
    automatic = True
    home = 0
    garot = xafs_garot
    inverted = ''
    flat = [0,0]
    y_uid = ''
    pitch_uid = ''
    f_uid = ''
    alignment_filename = ''
    _orientation = 'parallel'
    toss = os.path.join(user_ns['BMMuser'].folder, 'snapshots', 'toss.png')
    img = None

    @property
    def orientation(self):
        return self._orientation
    @orientation.setter
    def orientation(self, value):
        if value.lower() == 'perpendicular':
            self._orientation = 'perpendicular'
            user_ns['gawheel'].orientation = 'perpendicular'
        else:
            self._orientation = 'parallel'
            user_ns['gawheel'].orientation = 'parallel'

    
    def current(self):
        '''Return the current spinner number as an integer'''
        pos = self.garot.position
        cur = pos % 360
        here = (9-round(cur/45)) % 8
        if here == 0:
            here = 8
        return here

    def reset(self):
        '''Return glancing angle stage to spinner 1'''
        yield from self.alloff_plan()
        yield from mv(self.garot, self.home)
        report('Returned to spinner 1 at %d degrees and turned off all spinners' % self.home, level='bold')
        
    def valid(self, number=None):
        '''Check that an argument is an integer between 1 and 8'''
        if number is None:
            return False
        if type(number) is not int:
            return False
        if number < 1 or number > 8:
            return False
        return True
        
    def on(self, number):
        '''Turn on the specified spinner'''
        if self.spin is False:
            warning_msg('The spinners are currently disabled.  do "ga.spin = True" to re-enable.')
            return
        if not self.valid(number):
            error_msg('The fans are numbered from 1 to 8')
            return
        this = getattr(self, f'spinner{number}')
        this.put(1)
    def off(self, number=None):
        '''Turn off the specified spinner'''
        if number is None:
            self.alloff()
            return
        if not self.valid(number):
            error_msg('The fans are numbered from 1 to 8')
            return
        this = getattr(self, f'spinner{number}')
        this.put(0)
        
    def alloff(self):
        '''Turn off all spinners'''
        for i in range(1,9):
            self.off(i)
    def alloff_plan(self):
        '''Turn off all spinners as a plan'''
        save = user_ns['RE'].msg_hook
        user_ns['RE'].msg_hook = None
        for i in range(1,9):
            this = getattr(self, f'spinner{i}')
            yield from mv(this, 0)
        user_ns['RE'].msg_hook = save
            
    def to(self, number):
        '''Rotate to the specified spinner. Turn off all other spinners.  Turn
        on the specified spinner.'''
        if not self.valid(number):
            error_msg('The fans are numbered from 1 to 8')
            yield from null()
            return
        yield from self.alloff_plan()
        distance = number - self.current()
        if distance > 4:
            distance = distance - 8
        elif distance < -4:
            distance = 8 + distance
        angle = -45*distance
        yield from mvr(self.garot, angle)
        if self.spin is True:
            this = getattr(self, f'spinner{number}')
            yield from mv(this, 1)


    def align_pitch(self, force=False):
        '''Find the peak of xafs_pitch scan against It. Plot the
        result. Move to the peak.'''
        kafka.message({'close': 'last'})
        motor = user_ns['xafs_pitch']
        yield from prepare_alignment_scan()
        uid = yield from linescan(motor, 'it', -2.5, 2.5, 51, dopluck=False, force=force)
        kafka.message({'close': 'last'})

        
        kafka.message({'peakfit'    : True,
                       'uid'        : 'last',
                       'motor_name' : motor.name,
                       'signal'     : 'It',
                       'choice'     : 'peak',
                       'spinner'    : self.current()})
        top = fetch_peak_position_via_redis()
        yield from mv(motor, top)

        
        # ATTEMPTS = 4
        # error = None
        # for attempt in range(ATTEMPTS):
        #     try:
        #         table  = user_ns['db'][-1].table()
        #     except Exception as exc:
        #         print(f"glancing angle alignment: Failure {attempt} reading back pitch scan data:", repr(exc))
        #         error = exc
        #     else:
        #         break
        #     time.sleep(2)
        # else:
        #     # out of attemtps
        #     raise error
            
        # pitch  = table['xafs_pitch']
        # signal = table['It']/table['I0']
        # target = signal.idxmax()
        # yield from mv(xafs_pitch, pitch[target])
        # kafka.message({'glancing_angle' : 'pitch',
        #                'motor'          : 'xafs_pitch',
        #                'center'         : pitch[target],
        #                'amplitude'      : signal.max(),
        #                'spinner'        : self.current(),
        #                'xaxis'          : list(pitch),
        #                'data'           : list(signal),
        #                'uid'            : uid,})
        # #self.pitch_plot(pitch, signal)
    

    def align_linear(self, force=False, drop=None):
        '''Fit an error function to the linear scan against It. Plot the
        result. Move to the centroid of the error function.'''
        kafka.message({'close': 'last'})
        if self.orientation == 'parallel':
            motor = user_ns['xafs_liny']
        else:
            motor = user_ns['xafs_linx']
        yield from prepare_alignment_scan()
        uid = yield from linescan(motor, 'it', -2.3, 2.3, 51, dopluck=False)
        kafka.message({'close': 'last'})
        kafka.message({'stepfit'    : True,
                       'uid'        : 'last',
                       'motor_name' : motor.name,
                       'signal'     : 'It',
                       'spinner'    : self.current() })
        target = fetch_peak_position_via_redis(verbose=True)
        yield from mv(motor, target)
        
        
        # table  = user_ns['db'][-1].table()
        # yy     = table[motor.name]
        # signal = table['It']/table['I0']
        # if drop is not None:
        #     yy = yy[:-drop]
        #     signal = signal[:-drop]
        # if float(signal[2]) > list(signal)[-2] :
        #     ss     = -(signal - signal[2])
        #     self.inverted = 'inverted '
        # else:
        #     ss     = signal - signal[2]
        #     self.inverted    = ''
        # mod    = StepModel(form='erf')
        # pars   = mod.guess(ss, x=numpy.array(yy))
        # out    = mod.fit(ss, pars, x=numpy.array(yy))
        # whisper(out.fit_report(min_correl=0))
        # target = out.params['center'].value
        # yield from mv(motor, target)
        # kafka.message({'glancing_angle' : 'linear',
        #                'motor'          : motor.name,
        #                'center'         : target,
        #                'amplitude'      : out.params['amplitude'].value,
        #                'inverted'       : self.inverted,
        #                'spinner'        : self.current(),
        #                'xaxis'          : list(yy),
        #                'data'           : list(ss),
        #                'best_fit'       : list(out.best_fit),
        #                'uid'            : uid,})

        #self.y_plot(yy, out)


    def align_fluo(self, force=False):
        kafka.message({'close': 'last'})
        BMMuser = user_ns['BMMuser']
        yield from prepare_alignment_scan()
        if self.orientation == 'parallel':
            motor = user_ns['xafs_liny']
        else:
            motor = user_ns['xafs_linx']
        uid = yield from linescan(motor, 'xs', -1.8, 1.8, 51, dopluck=False, force=force, stack=False)
        kafka.message({'close': 'last'})
        kafka.message({'peakfit'    : True,
                       'uid'        : 'last',
                       'motor_name' : motor.name,
                       'signal'     : 'If',
                       'choice'     : 'peak',
                       'spinner'    : self.current()})
        target = fetch_peak_position_via_redis()
        yield from mv(motor, target)
        self.f_uid = uid

        # tf = user_ns['db'][-1].table()
        # yy = tf[motor.name]
        # yy = yy[2:]             # spurious first point in fluo scan due to Xspress3 issue 24 July 2024
        # rkvs = user_ns['rkvs']
        # detector = rkvs.get('BMM:xspress3').decode('utf-8')
        # if detector == '1':
        #     signal = tf[BMMuser.xs1] / tf['I0']
        # elif detector == '4':
        #     signal = (tf[BMMuser.xs1] +
        #               tf[BMMuser.xs2] +
        #               tf[BMMuser.xs3] +
        #               tf[BMMuser.xs4]) / tf['I0']
        # elif detector == '7':
        #     signal = (tf[BMMuser.xs1] +
        #               tf[BMMuser.xs2] +
        #               tf[BMMuser.xs3] +
        #               tf[BMMuser.xs4] +
        #               tf[BMMuser.xs5] +
        #               tf[BMMuser.xs6] +
        #               tf[BMMuser.xs7]) / tf['I0']
        # signal = signal[2:]
        # #if BMMuser.element in ('Cr', 'Zr'):
        # centroid = yy[signal.idxmax()]
        # #else:
        # #    com = int(center_of_mass(signal)[0])+1
        # #    centroid = yy[com]
        # yield from mv(motor, centroid)
        # kafka.message({'glancing_angle' : 'fluo',
        #                'motor'          : motor.name,
        #                'center'         : centroid,
        #                'amplitude'      : signal.max(),
        #                'inverted'       : self.inverted,
        #                'spinner'        : self.current(),
        #                'xaxis'          : list(yy),
        #                'data'           : list(signal),
        #                'uid'            : uid,})

        
    def auto_align(self, pitch=2, drop=None):
        '''Align a sample on a spinner automatically.  This performs 5 scans.
        The first four iterate twice between linear and pitch
        against the signal in It.  This find the flat position.

        Then the sample is pitched to the requested angle and a fifth
        scan is done to optimize the linear motor position against the
        fluorescence signal.

        The linear scans against It look like a step-down function.
        The center of this step is found as the centroid of a fitted
        error function.

        The xafs_pitch scan should be peaked.  Move to the max of the
        signal.

        The linear scan against fluorescence ideally looks like a
        flat-topped peak.  Move to the center of mass.

        At the end, a three-panel figure is drawn showing the last
        three scans.  This is posted to Slack.  It also finds its way
        into the dossier as a record of the quality of the alignment.

        Arguments
        =========
        pitch : int
          The angle at which to make the glancing angle measurements.
        drop : int or None
          If not None, then this many points will be dropped from the
          end of linear scan against transmission when fitting the error
          function. This is an attempt to deal gracefully with leakage 
          through the adhesive at very high energy.

        '''
        BMMuser = user_ns['BMMuser']

        def main_plan(pitch, drop):
            if BMMuser.macro_dryrun:
                report(f'Auto-aligning glancing angle stage, spinner {self.current()}', level='bold', slack=False)
                info_msg(f'\nBMMuser.macro_dryrun is True.  Sleeping for %.1f seconds at spinner %d.\n' %
                               (BMMuser.macro_sleep, self.current()))
                countdown(BMMuser.macro_sleep)
                return(yield from null())

            report(f'Auto-aligning glancing angle stage, spinner {self.current()}', level='bold', slack=True)

            suspenders.set_suspenders()
            self.alignment_filename = os.path.join(proposal_base(), 'snapshots', f'spinner{self.current()}-alignment-{now()}.png')
            kafka.message({'glancing_angle' : 'start',
                           'filename' : self.alignment_filename})

            ## first pass in transmission
            yield from self.align_linear(drop=drop)
            yield from sleep(1)
            yield from self.align_pitch()
            yield from sleep(1)

            ## for realsies X or Y in transmission
            yield from self.align_linear(drop=drop)
            #self.y_uid = user_ns['db'].v2[-1].metadata['start']['uid'] 
            yield from sleep(1)
            kafka.message({'close': 'all'})

            ## for realsies Y in pitch
            yield from self.align_pitch()
            #self.pitch_uid = user_ns['db'].v2[-1].metadata['start']['uid'] 
            yield from sleep(1)
            kafka.message({'close': 'all'})

            ## record the flat position
            if self.orientation == 'parallel':
                motor = user_ns['xafs_y']
            else:
                motor = user_ns['xafs_x']
            self.flat = [motor.position, user_ns['xafs_pitch'].position]

            ## move to measurement angle and align
            yield from mvr(user_ns['xafs_pitch'], pitch)
            yield from self.align_fluo()
            self.f_uid = user_ns['db'].v2[-1].metadata['start']['uid'] 


        def cleanup_plan():
            yield from mv(_locked_dwell_time, 0.5)
            yield from resting_state_plan()
            kafka.message({'close': 'all'})
            kafka.message({'glancing_angle' : 'stop'})
            self.y_uid     = user_ns['rkvs'].get('BMM:ga:xy_uid').decode('utf-8')
            self.pitch_uid = user_ns['rkvs'].get('BMM:ga:pitch_uid').decode('utf-8')
            self.f_uid     = user_ns['rkvs'].get('BMM:ga:fluo_uid').decode('utf-8')
            
        user_ns['RE'].msg_hook = None
        yield from finalize_wrapper(main_plan(pitch, drop), cleanup_plan())
        user_ns['RE'].msg_hook = BMM_msg_hook
        suspenders.clear_suspenders()
 


        
    def flatten(self):
        '''Return the stage to its nominally flat position.'''
        xafs_pitch = user_ns['xafs_pitch']
        if self.orientation == 'parallel':
            motor = user_ns['xafs_y']
        else:
            motor = user_ns['xafs_x']
        if self.flat != [0, 0]:
            yield from mv(motor, self.flat[0], xafs_pitch, self.flat[1])
        

    def dossier_entry(self):
        thistext  =  '	    <div>\n'
        thistext +=  '	      <h3>Instrument: Glancing angle stage</h3>\n'
        thistext +=  '	      <ul>\n'
        thistext += f'               <li><b>Spinner:</b> {self.current()}</li>\n'
        if self.automatic is True:
            thistext +=  '               <li><b>Alignment:</b> automatic</li>\n'
            thistext += f'               <li><b>Tilt angle:</b> {xafs_pitch.position - self.flat[1]:.1f}</li>\n'
        else:
            thistext +=  '               <li><b>Alignment:</b> manual</li>\n'
            thistext += f'               <li><b>Tilt angle:</b> {xafs_pitch.position:.1f}</li>\n'
        thistext += f'               <li><b>Spinning:</b> {"yes" if self.spin else "no"}</li>\n'
        thistext +=  '	      </ul>\n'
        thistext +=  '	    </div>\n'
        return thistext

    def ready_to_start(self):
        ## NEVER prompt when using queue server
        if is_re_worker_active() is True:
            user_ns['BMMuser'].prompt = False
        if user_ns['BMMuser'].prompt:
            print()
            action = animated_prompt('Is the glancing angle stage currently flat? ' + PROMPTNC)
            if action != '':
                if action[0].lower() != 'y':
                    return False
        return True

class GlancingAngleMacroBuilder(BMMMacroBuilder):
    '''A class for parsing specially constructed spreadsheets and
    generating macros for measuring XAS on the BMM glancing angle
    stage.

    Examples
    --------
    >>> gawheel = GlancingAngleMacroBuilder()
    >>> gawheel.spreadsheet('ga.xlsx')
    >>> gawheel.write_macro()

    '''
    macro_type = 'Glancing angle'
        
    def _write_macro(self):
        '''Write a macro paragraph for each sample described in the
        spreadsheet.  A paragraph consists of line to move to the
        correct spinner, lines to find or move to the center-aligned
        location in pitch and Y, lines to move to and from the correct
        glancing angle value, a line to change the edge energy (if
        needed), a line to measure the XAFS using the correct set of
        control parameters, and a line to close plot windows after the
        scan.
        '''
        element, edge, focus = (None, None, None)
        self.tab = ' '*8
        count = 0


        if self.nreps > 1:
            self.content += self.tab + f'for rep in range({self.nreps}):\n\n'
            self.tab = ' '*12
            #self.do_first_change = True
            self.content += self.check_edge()
        else:
            self.content += self.check_edge() + '\n'
            

        for m in self.measurements:

            if m['default'] is True:
                element = m['element']
                edge    = m['edge']
                continue
            if self.skip_row(m) is True:
                continue

            count += 1
            if self.nreps > 1:
                self.content += self.tab + f'report(f"{self.macro_type} sequence {{{count}+{int(self.calls_to_xafs/self.nreps)}*rep}} of {self.calls_to_xafs}", level="bold", slack=True)\n'
            else:
                self.content += self.tab + f'report("{self.macro_type} sequence {count} of {self.calls_to_xafs}", level="bold", slack=True)\n'

            #######################################
            # default element/edge(/focus) values #
            #######################################
            for k in ('element', 'edge', 'focus', 'method', 'spin', 'angle'):
                if m[k] is None:
                    m[k] = self.measurements[0][k]

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
                
            if m['element'] != element or m['edge'] != edge: # focus...
                element = m['element']
                edge    = m['edge']
                self.content += text
                self.totaltime += time
                
            else:
                if self.verbose:
                    self.content += self.tab + '## staying at %s %s\n' % (m['element'], m['edge'])
                pass

            #######################
            # move to next sample #
            #######################
            self.content += self.tab + f'ga.spin = {m["spin"]}\n'

            ###################################
            # move to correct slit dimensions #
            ###################################
            if m['slitheight'] is not None:
                if self.check_limit(user_ns['slits3'].vsize, m['slitheight']) is False: return(False)
                self.content += self.tab + f'yield from mv(slits3.vsize, {m["slitheight"]})\n'
            if m['slitwidth'] is not None:
                if self.check_limit(user_ns['slits3'].vsize, m['slitwidth']) is False: return(False)
                self.content += self.tab + f'yield from mv(slits3.hsize, {m["slitwidth"]})\n'

            #####################################
            # move to correct detector position #
            #####################################
            if m['detectorx'] is not None:
                if self.check_limit(user_ns['xafs_detx'], m['detectorx']) is False: return(False)
                self.content += self.tab + 'yield from mv(xafs_detx, %.2f)\n' % m['detectorx']
            self.content += self.tab + f'yield from mvr(xafs_detx, {self.retract})\n'
            if self.check_spinner(m['slot']) is False: return False
            self.content += self.tab + f'yield from ga.to({m["slot"]})\n'

            if self.orientation == "parallel":
                motor = 'xafs_y'
            else:
                motor = 'xafs_x'

            #############################################################
            # lower stage and measure reference channel for calibration #
            #############################################################
            fname = self.make_filename(m)
            self.content += self.tab + 'if ref is True:\n'
            self.content += self.tab + self.tab + f'yield from mvr({motor}, -{self.retract})\n'
            self.content += self.tab + self.tab + f'yield from xafs("{self.basename}.ini", mode="reference", filename="{m["element"]}_ref_{fname}", nscans=1, sample="{m["element"]} reference", element="{m["element"]}", edge="{m["edge"]}", bounds="-30 -10 40 70", steps="2 0.5 2", times="0.5 0.5 0.5", copy=False)\n'
            self.content += self.tab + self.tab + f'yield from mvr({motor}, {self.retract})\n'

            ####################################
            # move to correct height and pitch #
            ####################################
            if m['method'].lower() == 'automatic':
                user_ns['ga'].automatic = True
                if get_mode() == 'A':
                    self.content += self.tab + 'yield from mirror_pitch(mirror="m2", move=True)\n'
                else:
                    self.content += self.tab + 'yield from mirror_pitch(mirror="m3", move=True)\n'
                self.content += self.tab + f'yield from ga.auto_align(pitch={m["angle"]})\n'
            else:
                if m['sampley'] is not None:
                    if self.check_limit(user_ns['xafs_y'], m['sampley']) is False: return(False)
                    self.content += self.tab + f'yield from mv({motor}, {m["sampley"]})\n'
                if m['samplep'] is not None:
                    if self.check_limit(user_ns['xafs_pitch'], m['samplep']) is False: return(False)
                    self.content += self.tab + f'yield from mv(xafs_pitch, {m["samplep"]})\n'
                user_ns['ga'].automatic = False
            
            ############################################################
            # measure XAFS, then return to 0 pitch and close all plots #
            ############################################################
            self.content += self.tab + f'yield from mvr(xafs_detx, -{self.retract})\n'
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
            if m['method'].lower() == 'automatic':
                self.content += self.tab + f'yield from mvr(xafs_detx, {self.retract})\n'
                self.content += self.tab +  'yield from ga.flatten()\n'
                self.content += self.tab + f'yield from mvr(xafs_detx, -{self.retract})\n'
            self.content += self.tab + 'close_plots()\n\n'


            ########################################
            # approximate time cost of this sample #
            ########################################
            m['mode'] = 'fluorescence'  # always fluo in glancing angle
            self.estimate_time(m, element, edge)
            
        if self.nreps > 1:
            self.tab = ' ' * 8

        if self.close_shutters:
            self.content += self.tab +  'if not dryrun:\n'
            self.content += self.tab +  '    BMMuser.running_macro = False\n'
            self.content += self.tab +  '    suspenders.clear_suspenders()\n'
            self.content += self.tab +  '    yield from shb.close_plan()\n'

            


    def get_keywords(self, row, defaultline):
        '''Instructions for parsing spreadsheet columns into keywords.

        arguments
        ---------
        row : contents of a row as read by openpyxl, i.e. ws.rows
        defaultline : True only if this row contains the default
        parameters, i.e. the green row

        This must return a dictionary.  The dictionary keys are the
        keywords related to the column labels from the spreadsheet,
        the values are cell contents, possibly reduced to a specific
        type.

        '''
        this = {'default':    defaultline,
                'slot':       row[1].value,      # sample location
                'measure':    self.truefalse(row[2].value, 'measure'),  # filename and visualization
                'filename':   str(row[3].value),
                'nscans':     row[4].value,
                'start':      row[5].value,
                'spin':       self.truefalse(row[6].value, 'spin'),
                'element':    row[7].value,      # energy range
                'edge':       row[8].value,
                'focus':      row[9].value,
                'angle':      row[10].value,
                'sample':     self.escape_quotes(str(row[11].value)),     # scan metadata
                'prep':       self.escape_quotes(str(row[12].value)),
                'comment':    self.escape_quotes(str(row[13].value)),
                'bounds':     row[14].value,     # scan parameters
                'steps':      row[15].value,
                'times':      row[16].value,
                'detectorx':  row[17].value,
                'method':     row[18].value,
                'samplep':    row[19].value,     # other motors
                'sampley':    row[20].value,
                'slitwidth':  row[21].value,
                'slitheight': row[22].value,
                'snapshots':  self.truefalse(row[23].value, 'snapshots' ),  # flags
                'htmlpage':   self.truefalse(row[24].value, 'htmlpage'  ),
                'usbstick':   self.truefalse(row[25].value, 'usbstick'  ),
                'bothways':   self.truefalse(row[26].value, 'bothways'  ),
                'channelcut': self.truefalse(row[27].value, 'channelcut'),
                'ththth':     self.truefalse(row[28].value, 'ththth'    ),
                'url':        row[29].value,
                'doi':        row[30].value,
                'cif':        row[31].value, }
        return this

