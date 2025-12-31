import re
from BMM.macrobuilder import BMMMacroBuilder
from ophyd import EpicsSignal

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

class ResonantReflectivityMacroBuilder(BMMMacroBuilder):
    '''A class for parsing specially constructed spreadsheets and
    generating macros for measuring resonant reflectivity.

    Examples
    --------
    >>> refl = ResonantReflectivityMacroBuilder()
    >>> refl.spreadsheet('refl.xlsx')
    >>> refl.write_macro()

    '''
    macro_type = 'Resonant reflectivity'


    def __init__(self, *args, detector='pilatus', **kwargs):
        super().__init__(*args, **kwargs)

        self.detector = detector
        if detector == 'pilatus':
            pvbase = 'XF:06BMB-ES{Det:PIL100k}:'
        elif detector == 'eiger':
            pvbase = "XF:06BM-ES{Det-Eiger:1}"
            
        self.roi2_minx  = EpicsSignal(f'{pvbase}ROI2:MinX',  name='')
        self.roi2_sizex = EpicsSignal(f'{pvbase}ROI2:SizeX', name='')
        self.roi2_miny  = EpicsSignal(f'{pvbase}ROI2:MinY',  name='')
        self.roi2_sizey = EpicsSignal(f'{pvbase}ROI2:SizeY', name='')
        self.roi3_minx  = EpicsSignal(f'{pvbase}ROI3:MinX',  name='')
        self.roi3_sizex = EpicsSignal(f'{pvbase}ROI3:SizeX', name='')
        self.roi3_miny  = EpicsSignal(f'{pvbase}ROI3:MinY',  name='')
        self.roi3_sizey = EpicsSignal(f'{pvbase}ROI3:SizeY', name='')

        self.roistat2_minx  = EpicsSignal(f'{pvbase}ROIStat1:2:MinX',  name='')
        self.roistat2_sizex = EpicsSignal(f'{pvbase}ROIStat1:2:SizeX', name='')
        self.roistat2_miny  = EpicsSignal(f'{pvbase}ROIStat1:2:MinY',  name='')
        self.roistat2_sizey = EpicsSignal(f'{pvbase}ROIStat1:2:SizeY', name='')
        self.roistat3_minx  = EpicsSignal(f'{pvbase}ROIStat1:3:MinX',  name='')
        self.roistat3_sizex = EpicsSignal(f'{pvbase}ROIStat1:3:SizeX', name='')
        self.roistat3_miny  = EpicsSignal(f'{pvbase}ROIStat1:3:MinY',  name='')
        self.roistat3_sizey = EpicsSignal(f'{pvbase}ROIStat1:3:SizeY', name='')


        self.use_roi2 = EpicsSignal(f'{pvbase}ROIStat1:2:Use', name='use_roi2')
        self.use_roi3 = EpicsSignal(f'{pvbase}ROIStat1:3:Use', name='use_roi3')
    
    def screen_rois(self):
        self.roistat2_minx.put(self.roi2_minx.get())
        self.roistat2_sizex.put(self.roi2_sizex.get())
        self.roistat2_miny.put(self.roi2_miny.get())
        self.roistat2_sizey.put(self.roi2_sizey.get())
        self.roistat3_minx.put(self.roi3_minx.get())
        self.roistat3_sizex.put(self.roi3_sizex.get())
        self.roistat3_miny.put(self.roi3_miny.get())
        self.roistat3_sizey.put(self.roi3_sizey.get())

        print('Cut-n-paste these ROI values into your spreadsheet.\n')
        print(f'ROI2: {self.roi2_minx.get()} {self.roi2_sizex.get()} {self.roi2_miny.get()} {self.roi2_sizey.get()}')
        print(f'ROI3: {self.roi3_minx.get()} {self.roi3_sizex.get()} {self.roi3_miny.get()} {self.roi3_sizey.get()}')


    def set_rois(self, which, values):
        if which.lower() == 'roi2':
            mx, sx, my, sy = values # .split()
            self.roistat2_minx.put(int(mx))
            self.roi2_minx.put(int(mx))
            self.roistat2_sizex.put(int(sx))
            self.roi2_sizex.put(int(sx))
            self.roistat2_miny.put(int(my))
            self.roi2_miny.put(int(my))
            self.roistat2_sizey.put(int(sy))
            self.roi2_sizey.put(int(sy))
        elif which.lower() == 'roi3':
            mx, sx, my, sy = values # .split()
            self.roistat3_minx.put(int(mx))
            self.roi3_minx.put(int(mx))
            self.roistat3_sizex.put(int(sx))
            self.roi3_sizex.put(int(sx))
            self.roistat3_miny.put(int(my))
            self.roi3_miny.put(int(my))
            self.roistat3_sizey.put(int(sy))
            self.roi3_sizey.put(int(sy))
        else:
            print('Valid strings identifying the ROI are: roi2 roi3 ')

    def to_redis(self, flat=0, relativep=0):
        user_ns['rkvs'].set('bmm:reflectivity:flat', flat)
        user_ns['rkvs'].set('bmm:reflectivity:relativep', relativep)

    def dossier_entry(self):
        roi2 = [str(self.roistat2_minx.get()), str(self.roistat2_sizex.get()), str(self.roistat2_miny.get()), str(self.roistat2_sizey.get())]
        roi3 = [str(self.roistat3_minx.get()), str(self.roistat3_sizex.get()), str(self.roistat3_miny.get()), str(self.roistat3_sizey.get())]
        xafs_pitch = user_ns['xafs_pitch']
        
        thistext  =  '	    <div>\n'
        thistext +=  '	      <h3>Instrument: Resonant reflectivity</h3>\n'
        thistext +=  '	      <ul>\n'
        thistext += f'               <li><b>Detector:</b> {self.detector.capitalize()}</li>\n'
        thistext += f'               <li><b>Pitch:</b> {xafs_pitch.position:.3f}</li>\n'
        thistext += f'               <li><b>Flat:</b> {user_ns["rkvs"].get("bmm:reflectivity:flat").decode("utf-8")}'
        thistext += f'               <li><b>Relative pitch:</b> {user_ns["rkvs"].get("bmm:reflectivity:relativep").decode("utf-8")}'
        thistext += f'               <li><b>ROI2 definition:</b> {", ".join(roi2)}</li>\n'
        thistext += f'               <li><b>ROI3 definition:</b> {", ".join(roi3)}</li>\n'
        thistext +=  '	      </ul>\n'
        thistext +=  '	    </div>\n'
        return thistext

            
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


        self.content += self.tab + 'ga.spin = False\n\n'
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
            for k in ('element', 'edge', 'focus'):
                if m[k] is None:
                    m[k] = self.measurements[0][k]

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
            #if self.nonezero(m['spinner']):
            if self.check_spinner(m['spinner']) is False: return False
            self.content += self.tab + f'yield from ga.to({m["spinner"]})\n'
            if self.nonezero(m['samplex']):
                self.content += self.tab + f'yield from mv(xafs_x, {m["samplex"]})\n'
            if self.nonezero(m['sampley']):
                self.content += self.tab + f'yield from mv(xafs_y, {m["sampley"]})\n'

            ###################################
            # move to correct slit dimensions #
            ###################################
            if m['slitheight'] is not None:
                if self.check_limit(user_ns['slits3'].vsize, m['slitheight']) is False: return False
                self.content += self.tab + f'yield from mv(slits3.vsize, {m["slitheight"]})\n'
            if m['slitwidth'] is not None:
                if self.check_limit(user_ns['slits3'].vsize, m['slitwidth']) is False: return False
                self.content += self.tab + f'yield from mv(slits3.hsize, {m["slitwidth"]})\n'

            #####################################
            # move to correct detector position #
            #####################################
            if m['detectorx'] is not None:
                if self.check_limit(user_ns['xafs_detx'], m['detectorx']) is False: return False
                self.content += self.tab + f'yield from mv(xafs_detx, {m["detectorx"]:.3f})\n' 
            
            #########################################
            # move to correct pitch and y positions #
            #########################################
            self.content += self.tab + f'yield from mv(xafs_pitch, {m["samplep"]:.3f})\n'

            ####################
            # set Pilatus ROIs #
            ####################
            self.content += self.tab + f'refl.set_rois("roi2", {m["roi2"]})\n'
            self.content += self.tab + f'refl.set_rois("roi3", {m["roi3"]})\n'
            self.content += self.tab + f'refl.to_redis(flat={m["flat"]}, relativep={m["relativep"]})\n'
            
            ################
            # measure XAFS #
            ################
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


            ########################################
            # approximate time cost of this sample #
            ########################################
            m['mode'] = 'pilatus'  # always pilatus for resonant reflectivity
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
                'measure':    self.truefalse(row[2].value, 'measure'),  # filename and visualization
                'filename':   str(row[3].value),
                'nscans':     row[4].value,
                'start':      row[5].value,
                'element':    row[6].value,      # energy range
                'edge':       row[7].value,
                'focus':      row[8].value,
                'sample':     self.escape_quotes(str(row[9].value)),     # scan metadata
                'prep':       self.escape_quotes(str(row[10].value)),
                'comment':    self.escape_quotes(str(row[11].value)),
                'bounds':     row[12].value,     # scan parameters
                'steps':      row[13].value,
                'times':      row[14].value,
                'detectorx':  row[15].value,
                'flat':       row[16].value,
                'relativep':  row[17].value,
                'samplep':    row[18].value,     # other motors
                'sampley':    row[19].value,
                'samplex':    row[21].value,
                'spinner':    row[20].value,
                'roi2':       re.sub(r'\s+', ' ', re.sub(r',', ' ', str(row[22].value))).split(),
                'roi3':       re.sub(r'\s+', ' ', re.sub(r',', ' ', str(row[23].value))).split(),
                'slitwidth':  row[24].value,
                'slitheight': row[25].value,
                'snapshots':  self.truefalse(row[26].value, 'snapshots' ),  # flags
                'htmlpage':   self.truefalse(row[27].value, 'htmlpage'  ),
                'usbstick':   self.truefalse(row[28].value, 'usbstick'  ),
                'bothways':   self.truefalse(row[29].value, 'bothways'  ),
                'channelcut': self.truefalse(row[30].value, 'channelcut'),
                'ththth':     self.truefalse(row[31].value, 'ththth'    ),
                'url':        row[32].value,
                'doi':        row[33].value,
                'cif':        row[34].value, }
        if this['default'] is True:
            this['mode'] = 'pilatus'  # there is no mode column in the res refl spreadsheet
        return this

