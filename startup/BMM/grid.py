from BMM.macrobuilder import BMMMacroBuilder

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from rich import print as cprint

class GridMacroBuilder(BMMMacroBuilder):
    '''A class for parsing specially constructed spreadsheets and
    generating macros for measuring XAS using the Linkam stage.

    Examples
    --------
    >>> gmb = GridMacroBuilder()
    >>> gmb.spreadsheet('grid.xlsx')
    >>> gmb.write_macro()

    '''
    macro_type = 'Grid'
    motor1     = None
    motor2     = None
    motor3     = None
    motor4     = None
    motor5     = None
    position1  = None
    position2  = None
    position3  = None
    position4  = None
    position5  = None

    
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
            self.content = self.tab + f'for rep in range({self.nreps}):\n\n'
            self.tab = ' '*12
            #self.do_first_change = True
            self.content += self.check_edge()
        else:
            self.content += self.check_edge()
            
        
        for m in self.measurements:

            if m['default'] is True:
                element     = m['element']
                edge        = m['edge']
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
            for k in ('element', 'edge', 'focus', 'motor1', 'motor2', 'motor3'):
                if m[k] is None:
                    m[k] = self.measurements[0][k]

            ############################
            # sample and slit movement #
            ############################
            if m['detectorx'] is not None:
                if self.check_limit(user_ns['xafs_detx'], m['detectorx']) is False: return False
                self.content += self.tab + f'yield from mv(xafs_detx, {m["detectorx"]:.2f})\n'

            ## 2 mandatory motors
            if m['position1'] is not None and m['position2'] is not None:
                if self.check_limit(m['motor1'], m['position1']) is False: return False
                if self.check_limit(m['motor2'], m['position2']) is False: return False
                self.content += self.tab + f'gmb.motor1, gmb.motor2, gmb.position1, gmb.position2 = {m["motor1"]}, {m["motor2"]}, {m["position1"]}, {m["position2"]}\n'
                self.content += self.tab + f'yield from mv({m["motor1"]}, {m["position1"]:.3f}, {m["motor2"]}, {m["position2"]:.3f})\n'
                self.motor1    = m["motor1"]
                self.position1 = m["position1"]
                self.motor2    = m["motor2"]
                self.position2 = m["position2"]
            else:
                if m['position1'] is not None:
                    if self.check_limit(m['motor1'], m['position1']) is False: return False
                    self.motor1    = m["motor1"]
                    self.position1 = m["position1"]
                    self.content += self.tab + f'gmb.motor1, gmb.position1 = {m["motor1"]}, {m["position1"]}\n'
                    self.content += self.tab + f'yield from mv({m["motor1"]}, {m["position1"]:.3f})\n'
                if m['position2'] is not None:
                    if self.check_limit(m['motor2'], m['position2']) is False: return False
                    self.motor2    = m["motor2"]
                    self.position2 = m["position2"]
                    self.content += self.tab + f'gmb.motor2, gmb.position2 = {m["motor2"]}, {m["position2"]}\n'
                    self.content += self.tab + f'yield from mv({m["motor2"]}, {m["position2"]:.3f})\n'

            ## 3 optional motors
            for i in (3,4,5):
                thismotor = m[f'motor{i}']
                thisposition = m[f'position{i}']
                if thismotor is not None and thisposition is not None:
                    if self.check_limit(m['motor3'], m['position3']) is False:
                        return False
                    self.content += self.tab + f'gmb.motor{i}, gmb.position{i} = {thismotor}, {thisposition}\n'
                    if thismotor == 'xafs_garot':
                        if int(thisposition) < 1 or int(thisposition) > 8:
                            cprint(f'\n[yellow2]{m["filename"]}: xafs_garot positions must be in the range 1-8 (inclusive)[/yellow2]\n')
                            raise ValueError('xafs_garot positions must be in the range 1-8 (inclusive)')
                        self.content += self.tab + f'yield from ga.to({int(thisposition)})\n'
                    elif thismotor == 'xafs_wheel':
                        if int(thisposition) < 1 or int(thisposition) > 8:
                            cprint(f'\n[yellow2]{m["filename"]}: xafs_wheel positions must be in the range 1-24 (inclusive)[/yellow2]\n')
                            raise ValueError('xafs_wheel positions must be in the range 1-24 (inclusive)')
                        self.content += self.tab + f'yield from slot({int(thisposition)})\n'
                    elif thismotor == 'xafs_ref':
                        raise ValueError('Not currently using xafs_ref in grid automation.')
                    else:
                        self.content += self.tab + f'yield from mv({thismotor}, {thisposition:.3f})\n'
                        
                    setattr(self, f'motor{i}', thismotor)
                    setattr(self, f'position{i}', thisposition)

            if m['slitwidth'] is not None:
                if self.check_limit(user_ns['slits3'].hsize, m['slitwidth']) is False: return False
                self.content += self.tab + 'yield from mv(slits3.hsize, %.2f)\n' % m['slitwidth']
            if m['slitheight'] is not None:
                if self.check_limit(user_ns['slits3'].vsize, m['slitheight']) is False: return False
                self.content += self.tab + 'yield from mv(slits3.vsize, %.2f)\n' % m['slitheight']
                

            
            ##########################
            # change edge, if needed #
            ##########################
            focus = False
            if m['focus'] == 'focused':
                focus = True
            text, time, inrange = self.do_change_edge(m['element'], m['edge'], focus, self.tab)
            if inrange is False: return False
                            
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
                if type(m[k]) is str and m[k] == 'None':
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

            ########################################
            # approximate time cost of this sample #
            ########################################
            self.estimate_time(m, element, edge)

        if self.nreps > 1:
            self.tab = ' ' * 8

        if self.close_shutters:
            self.content += self.tab + 'if not dryrun:\n'
            self.content += self.tab + '    BMMuser.running_macro = False\n'
            self.content += self.tab + '    suspenders.clear_suspenders()\n'
            self.content += self.tab + '    yield from shb.close_plan()\n'

    def dossier_entry(self):
        thistext  =  '	    <div>\n'
        thistext +=  '	      <h3>Instrument: Motor grid</h3>\n'
        thistext +=  '	      <ul>\n'
        thistext += f'               <li><b>Motor 1:</b> {self.motor1.name} = {self.position1:.3f}</li>\n'
        thistext += f'               <li><b>Motor 2:</b> {self.motor2.name} = {self.position2:.3f}</li>\n'
        if type(self.position3) is not int and self.position3 is not None:
            thistext += f'               <li><b>Motor 3:</b> {self.motor3.name} = {self.position3:.3f}</li>\n'
        thistext +=  '	      </ul>\n'
        thistext +=  '	    </div>\n'
        return thistext

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

        version = int(self.version.split()[1])
        if version <= 16:
            raise ValueError('You MUST use at least version 17 of the grid spreadsheet! (as of 2025-07-07))')
        
        this = {'default':     defaultline,
                'measure':     self.truefalse(row[2].value, 'measure'), # filename and visualization
                'filename':    str(row[3].value),
                'nscans':      row[4].value,
                'start':       row[5].value,
                'mode':        row[6].value,
                'element':     row[7].value,      # energy range
                'edge':        row[8].value,
                'focus':       row[9].value,
                'sample':      self.escape_quotes(str(row[10].value)),     # scan metadata
                'prep':        self.escape_quotes(str(row[11].value)),
                'comment':     self.escape_quotes(str(row[12].value)),
                'bounds':      str(row[13].value),     # scan parameters
                'steps':       str(row[14].value),
                'times':       str(row[15].value),
                'detectorx':   row[16].value,
                'motor1':      self.motor1,     # motor names and positions 
                'position1':   self.nonezero(row[17].value),
                'motor2':      self.motor2,
                'position2':   self.nonezero(row[18].value),
                'motor3':      self.motor3,
                'position3':   self.nonezero(row[19].value),
                'motor4':      self.motor4,
                'position4':   self.nonezero(row[20].value),
                'motor5':      self.motor5,
                'position5':   self.nonezero(row[21].value),
                'slitwidth':   row[22].value,
                'slitheight':  row[23].value,
                'snapshots':   self.truefalse(row[24].value, 'snapshots' ),  # flags
                'htmlpage':    self.truefalse(row[25].value, 'htmlpage'  ),
                'usbstick':    self.truefalse(row[26].value, 'usbstick'  ),
                'bothways':    self.truefalse(row[27].value, 'bothways'  ),
                'channelcut':  self.truefalse(row[28].value, 'channelcut'),
                'ththth':      self.truefalse(row[29].value, 'ththth'    ),
                'url':         row[30].value,
                'doi':         row[31].value,
                'cif':         row[32].value, }
        return this
