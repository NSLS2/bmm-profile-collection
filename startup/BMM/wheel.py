import sys, os.path, re
#import pprint
#pp = pprint.PrettyPrinter(indent=4)
from openpyxl import load_workbook
import configparser
import numpy

from bluesky.plan_stubs import null, sleep, mv, mvr

from BMMCommon.tools.messages import *  # error_msg et al. + boxedtext
from BMMCommon.tools.periodictable import PERIODIC_TABLE, edge_energy

from BMM.functions      import present_options
from BMM.macrobuilder   import BMMMacroBuilder
from BMM.motors         import EndStationEpicsMotor
from BMM.logging        import report
from BMM.xafs_functions import conventional_grid
from BMM.workspace      import rkvs

from BMM.user_ns.base import profile_configuration

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

class WheelMotor(EndStationEpicsMotor):
    '''Motor class for BMM sample wheels.
    
    These wheels have 48 slots, in two rings of 24 slots spaced 15 degrees apart.

    When instantiated, you must set these attributes:

    x_motor : (EpicsMotor) 
        The motor that translates the wheel between rings

    outer_position : (float)
        The position of the outer ring on that motor

    inner_position : (float)
        The position of the inner ring on that motor

    slotone : (float)
        The position of the first slot in user units on the wheel motor

    See BMM/user_ns/instruments.py for examples of the use of this motor class

    Methods
    -------
    current_slot() :
        return the current slot number, even if the wheel has rotated many times

    set_slot(n) :
        move to the given slot number, taking care to go the shorter way

    reset() :
        return the wheel to 0

    '''
    def describe(self):
        res = super().describe()
        try:
            res['xafs_wheel_user_setpoint']['dtype_str'] = '<f8'
        except:
            pass
        return res
    
    def current_slot(self, value=None):
        '''Return the current slot number for a sample wheel.'''
        if value is not None:
            angle = round(value)
        else:
            angle = round(self.user_readback.get())
        this = round((-1*self.slotone-15+angle) / (-15)) % 24
        if this == 0: this = 24
        return this

    def reset(self):
        '''Return a sample wheel to slot 1'''
        yield from mv(self, self.slotone)
        report('Returned to sample wheel slot 1 at %d degrees' % self.slotone, level='bold')

    def angle_from_current(self, n):
        '''Return the angle from the current position to the target'''
        if type(n) is not int:
            error_msg('Slots numbers must be integers between 1 and 24 (argument was %s)' % type(n))
            return(0)
        if n<1 or n>24:
            error_msg('Slots are numbered from 1 to 24')
            return(0)

        current = self.current_slot()
        distance = n - current
        if distance > 12:
            distance = distance - 24
        elif distance < -12:
            distance = 24 + distance
        angle = -15*distance
        return angle
        
    def set_slot(self, n):
        '''Move to a numbered slot on a sample wheel.'''
        if type(n) is not int:
            error_msg('Slots numbers must be integers between 1 and 24 (argument was %s)' % type(n))
            return(yield from null())
        if n<1 or n>24:
            error_msg('Slots are numbered from 1 to 24')
            return(yield from null())
        angle = self.angle_from_current(n)
        yield from mvr(self, angle)
        report('Arrived at sample wheel slot %d' % n, level='bold')

    def slot_number(self, target=None):
        try:
            target = target.capitalize()
            slot = xafs_ref.mapping[target][1]
            return slot
        except:
            return self.current_slot()

    def position_of_slot(self, target):
        if type(target) is int:
            angle = self.angle_from_current(target)
        elif type(target) is str:
            target = self.slot_number(target.capitalize())
            angle = self.angle_from_current(target)
        return(self.user_readback.get()+angle)

    def recenter(self):
        here = self.user_readback.get()
        center = round(here / 15) * 15
        yield from mv(self, center)
        whisper('recentered %s to %.1f' % (self.name, center))

    def in_place(self):
        #user_ns['wmb'].outer_position = user_ns['xafs_x'].position
        #user_ns['wmb'].inner_position = user_ns['wmb'].outer_position + 26
        self.outer_position = self.x_motor.position
        self.inner_position = self.outer_position + 26
        if self.x_motor.name == 'xafs_x':
            rkvs.set('BMM:wheel:outer', self.outer_position)
        elif self.x_motor.name == 'xafs_refx':
            rkvs.set('BMM:ref:outer', self.outer_position)

    def inner(self):
        yield from mv(self.x_motor, self.inner_position)

    def outer(self):
        yield from mv(self.x_motor, self.outer_position)

    def slot_ring(self):
        if 'double' in user_ns['BMMuser'].instrument:
            if abs(self.x_motor.position - self.inner_position) < 5.0:
                return 'inner'
            else:
                return 'outer'
        else:
            return 'outer'

    def dossier_entry(self):
        thistext  =  '	    <div>\n'
        thistext +=  '	      <h3>Instrument: Ex situ sample wheel</h3>\n'
        thistext +=  '	      <ul>\n'
        thistext += f'               <li><b>Slot number:</b> {self.current_slot()}</li>\n'
        thistext += f'               <li><b>Ring:</b> {self.slot_ring()}</li>\n'
        thistext +=  '	      </ul>\n'
        thistext +=  '	    </div>\n'
        return thistext



def reference(target=None):
    xafs_ref  = user_ns['xafs_ref']
    xafs_refx = user_ns['xafs_refx']
    if type(target) is not str:
        print('Target must be a 1- or 2-letter element symbol. Not moving reference wheel.')
        return(yield from null())
    target = target.capitalize()
    if target not in PERIODIC_TABLE:
        print('Target must be a 1- or 2-letter element symbol. Not moving reference wheel.')
        return(yield from null())
    if target not in xafs_ref.mapping:
        print(f'{target} is not configured on the reference wheel. Not moving reference wheel.')
        return(yield from null())
    ring = xafs_ref.mapping[target][0]
    slot = xafs_ref.mapping[target][1]
    if ring == 1:
        #xafs_refx.user_setpoint.set(xafs_ref.inner_position)
        yield from mv(xafs_refx, xafs_ref.inner_position)
    else:
        #xafs_refx.user_setpoint.set(xafs_ref.outer_position)
        yield from mv(xafs_refx, xafs_ref.outer_position)
    yield from xafs_ref.set_slot(slot)
    #whisper('Pausing for 15 seconds to make sure xafs_refx is done moving.')
    #yield from mv(user_ns['busy'], 15)


def determine_reference():
    if profile_configuration.getboolean('experiments', 'use_reference') is False:
        return 'None'
    xafs_ref  = user_ns['xafs_ref']
    xafs_refx = user_ns['xafs_refx']
    slot  = round((-15+xafs_ref.position) / (-15)) % 24
    if abs(xafs_refx.position - xafs_ref.outer_position) < abs(xafs_refx.position - xafs_ref.inner_position):  # if closer to outer ring...
        ring = 0
    else:
        ring = 1
    for k in xafs_ref.mapping.keys():
        if xafs_ref.mapping[k][0] == ring and xafs_ref.mapping[k][1] == slot and 'empty' not in k:
            return k
    return 'None'
        

def show_reference_wheel():
    xafs_ref = user_ns['xafs_ref']

    def write_text(current_ref, k):
        if k in ('Th', 'U', 'Pu', 'Tc', 'Am'):  # skip elements which use other elements
            return('')
        elif k == current_ref:                  # green: current position of reference wheel
            return('[yellow1]%4.4s[/yellow1]   ' % k)
        elif xafs_ref.mapping[k][3] == 'None':  # gray: defined position, missing reference
            return('[bold black]%4.4s[/bold black]   ' % k)
        else:
            here = k
            if 'empty' in k:
                here = 'None'
            return('%4.4s' % here + '   ')
    
    this  = determine_reference()
    text = ''
    for i, which in enumerate(['outer', 'inner']):
        text = '[bold green]    1      2      3      4      5      6      7      8      9     10     11     12[/bold green]\n '
        for k in xafs_ref.mapping.keys():
            if xafs_ref.mapping[k][4] is False:
                continue
            if xafs_ref.mapping[k][0] == i and xafs_ref.mapping[k][1] < 13:
                text += write_text(this,k)
        text += '\n'
        text += '[bold green]   13     14     15     16     17     18     19     20     21     22     23     24\n[/bold green] '
        for k in xafs_ref.mapping.keys():
            if xafs_ref.mapping[k][4] is False:
                continue
            if xafs_ref.mapping[k][0] == i and xafs_ref.mapping[k][1] > 12:
                text += write_text(this,k)
        if i == 0:
            print()

        boxedtext(text, title=f"Reference wheel, {which} ring", color='yellow')

    if profile_configuration.getboolean('experiments', 'radiological') is True:
        text = ''
        for key in profile_configuration['experiments'].keys():
            if '_ref' not in key:
                continue
            elem = key.split('_')[0].capitalize()
            if elem == user_ns['BMMuser'].element:
                elem = f'[yellow1]{elem}[/yellow1]'
            data = profile_configuration.get('experiments', key).split()
            ring = 'outer'
            if data[0] == 1:
                ring = 'inner'
            slot = data[1]
            text += f'  {elem:>2}: {ring} ring, slot [white]{slot}[/white]\n'
        boxedtext(text[:-1], title=f"Radiological reference wheel", color='yellow')

    

        
class WheelMacroBuilder(BMMMacroBuilder):
    '''A class for parsing specially constructed spreadsheets and
    generating macros for measuring XAS on the BMM wheel.

    Examples
    --------
    >>> mb = MacroBuilder()
    >>> mb.spreadsheet('wheel1.xlsx')
    >>> mb.write_macro()
    '''
    macro_type = 'Wheel'
    
    def _write_macro(self):
        '''Write a macro paragraph for each sample described in the
        spreadsheet.  A paragraph consists of line to move to the
        correct wheel slot, a line to change the edge energy (if
        needed), a line to measure the XAFS using the correct set of
        control parameters, and a line to close plot windows after the
        scan.  

        Finally, write out the master INI and macro python files.
        '''
        element, edge, focus, slot = (None, None, None, None)
        self.tab = ' '*8
        count = 0

        if self.nreps > 1:
            self.content += self.tab + f'for rep in range({self.nreps}):\n\n'
            self.tab = ' '*12
            #self.do_first_change = True
            self.content += self.check_edge()
        else:            
            self.content += self.check_edge()
            
        for m in self.measurements:

            if m['default'] is True:
                element = m['element']
                edge    = m['edge']
                slot    = 0
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
            for k in ('element', 'edge', 'focus'):
                if m[k] is None:
                    m[k] = self.measurements[0][k]

            ############################
            # sample and slit movement #
            ############################
            if m['slot'] != slot:
                self.content += self.tab + 'yield from slot(%d)\n' % m['slot']
                slot = m['slot']
            if 'ring' in m:
                #here = user_ns['xafs_det'].position
                #if here < 150:
                #    self.content += self.tab + 'yield from mvr(xafs_det, 20)\n'
                if m['ring'].lower() == 'inner':
                    self.content += self.tab + f'yield from xafs_wheel.inner() # inner ring\n'
                else:
                    self.content += self.tab + f'yield from xafs_wheel.outer() # outer ring\n'
                #if here < 150:
                #    self.content += self.tab + f'yield from mv(xafs_det, {here:.3f})\n'
            if m['samplex'] is not None:
                if self.check_limit(user_ns['xafs_x'], m['samplex']) is False: return(False)
                self.content += self.tab + 'yield from mv(xafs_x, %.3f)\n' % m['samplex']
            if m['sampley'] is not None:
                if self.check_limit(user_ns['xafs_y'], m['sampley']) is False: return(False)
                self.content += self.tab + 'yield from mv(xafs_y, %.3f)\n' % m['sampley']
            if m['slitwidth'] is not None:
                if self.check_limit(user_ns['slits3'].hsize, m['slitwidth']) is False: return(False)
                self.content += self.tab + 'yield from mv(slits3.hsize, %.2f)\n' % m['slitwidth']
            if m['slitheight'] is not None:
                if self.check_limit(user_ns['slits3'].vsize, m['slitheight']) is False: return(False)
                self.content += self.tab + 'yield from mv(slits3.vsize, %.2f)\n' % m['slitheight']
            if m['detectorx'] is not None:
                if self.check_limit(user_ns['xafs_detx'], m['detectorx']) is False: return(False)
                self.content += self.tab + 'yield from mv(xafs_detx, %.2f)\n' % m['detectorx']
            # if m['optimize'] is not None:  # parse optimize xfstring, which is something like "max fluo(X)"
            #     do_max = 'max' if 'max'  in m['optimize'] else 'min'
            #     optdet = 'If'  if 'fluo' in m['optimize'] else 'It'
            #     (optmotor, startstop) = ('xafs_x', 3) if '(X)' in m['optimize'] else ('xafs_y', 10)
            #     self.content += self.tab + f'yield from peak_scan(motor={optmotor}, start=-{startstop}, stop={startstop}, nsteps=41, detector={optdet}, find=\'{do_max}\')\n'
            #     self.totaltime += 1.0

            
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
                    #continue
                    m[k] = None
                ## if a cell has data, put it in the argument list for xafs()
                if m[k] is not None and m[k] != '':
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

            ########################################
            # approximate time cost of this sample #
            ########################################
            if element is None:
                element = self.measurements[0]['element']
            if edge is None:
                edge = self.measurements[0]['edge']
            self.estimate_time(m, element, edge)
            
        if self.nreps > 1:
            self.tab = ' ' * 8
            
        if self.close_shutters:
            self.content += self.tab + 'if not dryrun:\n'
            self.content += self.tab + '    BMMuser.running_macro = False\n'
            self.content += self.tab + '    BMM_clear_suspenders()\n'
            self.content += self.tab + '    yield from shb.close_plan()\n'
        return(True)


    def get_keywords(self, row, defaultline):
        ## self.offset would be 1 for a very old spreadsheet that has
        ## an explicit e0 column.  This should be deprecated.
        this = {'default' :   defaultline,
                'slot':       row[1].value,      # sample location
                'ring':       row[2].value,
                'measure':    self.truefalse(row[3].value, 'measure'),  # filename and visualization
                'filename':   str(row[4].value),
                'nscans':     row[5].value,
                'start':      row[6].value,
                'mode':       row[7].value,
                'element':    row[8].value,      # energy range
                'edge':       row[9].value,
                'focus':      row[10].value,
                'sample':     self.escape_quotes(str(row[11].value)),     # scan metadata
                'prep':       self.escape_quotes(str(row[12].value)),
                'comment':    self.escape_quotes(str(row[13].value)),
                'bounds':     row[14].value,     # scan parameters
                'steps':      row[15].value,
                'times':      row[16].value,
                'detectorx':  row[17].value,
                'samplex':    row[18].value,     # other motors 
                'sampley':    row[19].value,
                'slitwidth':  row[20].value,
                'slitheight': row[21].value,
                'snapshots':  self.truefalse(row[22].value, 'snapshots' ), # flags
                'htmlpage':   self.truefalse(row[23].value, 'htmlpage'  ),
                'usbstick':   self.truefalse(row[24].value, 'usbstick'  ),
                'bothways':   self.truefalse(row[25].value, 'bothways'  ),
                'channelcut': self.truefalse(row[26].value, 'channelcut'),
                'ththth':     self.truefalse(row[27].value, 'ththth'    ),
                'url':        row[28].value,
                'doi':        row[29].value,
                'cif':        row[30].value, }
        return {**this}
        # if self.do_opt:
        #     rightend = {'optimize':   row[22].value,
        #                 'snapshots':  self.truefalse(row[23].value, 'snapshots' ), # flags
        #                 'htmlpage':   self.truefalse(row[24].value, 'htmlpage'  ),
        #                 'usbstick':   self.truefalse(row[25].value, 'usbstick'  ),
        #                 'bothways':   self.truefalse(row[26].value, 'bothways'  ),
        #                 'channelcut': self.truefalse(row[27].value, 'channelcut'),
        #                 'ththth':     self.truefalse(row[28].value, 'ththth'    ),
        #                 'url':        row[29].value,
        #                 'doi':        row[30].value,
        #                 'cif':        row[31].value, }
        # else:
        #     rightend = {'optimize' :  None,
        #                 'snapshots':  self.truefalse(row[22], 'snapshots' ), # flags
        #                 'htmlpage':   self.truefalse(row[23], 'htmlpage'  ),
        #                 'usbstick':   self.truefalse(row[24], 'usbstick'  ),
        #                 'bothways':   self.truefalse(row[25], 'bothways'  ),
        #                 'channelcut': self.truefalse(row[26], 'channelcut'),
        #                 'ththth':     self.truefalse(row[27], 'ththth'    ),
        #                 'url':        row[28].value,
        #                 'doi':        row[29].value,
        #                 'cif':        row[30].value, }
        # return {**this, **rightend}
                         
            
