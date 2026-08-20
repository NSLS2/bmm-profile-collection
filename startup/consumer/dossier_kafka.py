import os, sys, re, socket, json, datetime, pathlib, uuid
from urllib.parse import quote
import numpy, pandas, openpyxl
from scipy.io import savemat
from bluesky import __version__ as bluesky_version
import traceback

from databroker.queries import TimeRange
from tiled.queries import FullText, Regex, Eq


from pygments import highlight
from pygments.lexers import PythonLexer, IniLexer
from pygments.formatters import HtmlFormatter

from bmm_tools.tools.periodictable import edge_energy, Z_number, element_symbol, element_name
from tools import echo_slack, experiment_folder, file_resource, profile_configuration, rkvs
from slack import img_to_slack, post_to_slack
from nslsii.utils import open_redis_client


if not os.environ.get('AZURE_TESTING'):
    #redis_host = profile_configuration['services']['bmm_redis']
    redis_host = profile_configuration['services']['nsls2_redis']
    redis_port = profile_configuration['services']['redis_port']
    redis_ssl  = profile_configuration['services']['redis_ssl']
    redis_db   = profile_configuration['services']['bmm_redis']
else:
    redis_host = '127.0.0.1'
class NoRedis():
    def set(self, thing, otherthing):
        return None
    def get(self, thing):
        return None
# try:
#     rkvs = open_redis_client(redis_host, redis_port, redis_ssl, redis_db=redis_db)
#     #rkvs = redis.Redis(host=redis_host, port=6379, db=0)
# except Exception as E:
#     print(E)
#     rkvs = NoRedis()

all_references = json.loads(rkvs['BMM:reference:mapping'].decode('UTF8'))


startup_dir = profile_configuration['services']['startup']

def log_entry(logger, message):
    #if logger.name == 'BMM file manager logger' or logger.name == 'bluesky_kafka':
    #print(message)
    post_to_slack(message)
    echo_slack(text = message,
               icon = 'message',
               rid  = None )
    logger.info(message)


class BMMDossier():
    '''A class for generating a static HTML file for documenting an XAS
    measurement at BMM.

    The concept is that most of the metadata needed for the dossier
    will be accumulated in the start document of each scan in the
    sequence.  At the end of the scan sequence, the static HTML file
    will be generated.

    A small number of things are accumulated as objects of the class
    as the scan sequence progresses.  Thus there are some small
    differences between a dossier generated at the time of measurement
    and one made after the fact.

    That static HTML file is made using a set of simple text templates
    which are filled in, then concatenated in a way that suitable for
    the current XAS measurement.

    It is the responsibility of the process sending the kafka messages
    to supply each of the attributes listed below.  All other metadata
    will be found in the start document.

    attributes
    ==========
    folder : str
      target data folder, if None use folder recorded in start doc
      if provided, this is the base folder on central storage for 
      this experiment
    rid : str
      reference ID number for the link to Slack message log capture. 
      This is only relevant during an experiment when Slack messages 
      are being captured in the timeline file.
    uidlist : list of str (or str)
      list of XAFS scan UIDs in the scan sequence
      if a string is provided, it will be converted in a list of length 1
    uid : str
      XAFS scan UID.  If provided this way, it will be appended to 
      self.uidlist

    methods
    =======
    write_dossier
       generate the sample specific dossier file for an XAFS measurement

    raster_dossier
       generate the sample specific dossier file for a raster measurement
    
    sead_dossier
       generate the sample specific dossier file for a SEAD measurement
    
    write_manifest
       update the manifest and the 00INDEX.html file


    instrument state
    ================

    If using one of the automated instruments (sample wheel, Linkam
    stage, LakeShore temperature controller, glancing angle stage,
    motor grid), the class implementing the instrument is responsible
    for supplying a method called dossier_entry.  Here is an example
    from the glancing angle stage class:

           def dossier_entry(self):
              thistext  =  '	    <div>\n'
              thistext +=  '	      <h3>Instrument: Glancing angle stage</h3>\n'
              thistext +=  '	      <ul>\n'
              thistext += f'               <li><b>Spinner:</b> {self.current()}</li>\n'
              thistext += f'               <li><b>Tilt angle:</b> {xafs_pitch.position - self.flat[1]:.1f}</li>\n'      
              thistext += f'               <li><b>Spinning:</b> {"yes" if self.spin else "no"}</li>\n'
              thistext +=  '	      </ul>\n'
              thistext +=  '	    </div>\n'
              return thistext

    This returns a <div> block for the HTML dossier file.  The
    contents of this text include a header3 description of the
    instrument and an unordered list of the most salient aspects of
    the current state of the instrument.

    Admittedly, requiring a method that generates suitable HTML is a
    bit unwieldy, but is allows much flexibility in how the instrument
    gets described in the dossier.

    The dossier_entry methods get used in BMM/xafs.py around line 880.

    The methods raster_dossier and sead_dossier serve this purpose for
    those measurements.

    '''

    instrument = None
    rid = None
    uidlist = []
    force_seqnumber = None

    def set_parameters(self, **kwargs):
        '''Set dossier parameters from strings in the Kafka message.

        Take care with "uid" / "uidlist" to be sure that strings are
        turned into lists of strings.  All dossier methods expect that
        self.uidlist is a list of strings, even if it is a list of one.

        '''
        for k in kwargs.keys():
            if k == 'dossier':
                continue
            elif k == 'uid':
                self.uidlist.append(kwargs['uid'])
            elif k == 'uidlist' and type(kwargs['uidlist']) is str:
                self.uidlist = [kwargs['uidlist'],]
            else:
                setattr(self, k, kwargs[k])


    def write_dossier(self, bmm_catalog, logger):
        '''
        Write a dossier for an XAFS scan sequence.
        '''

        if len(self.uidlist) == 0:
            log_entry(logger, '*** cannot write dossier, uidlist is empty')
            return None

        ## gather information for the dossier from the start document
        ## of the first scan in the sequence
        XDI = bmm_catalog[self.uidlist[0]].start['XDI']
        if '_snapshots' in XDI:
            snapshots = XDI['_snapshots']
        else:
            snapshots = {}
        

        folder = self.folder
        if folder is None or folder == '':
            folder = XDI["_user"]["folder"]
        ## determine folder from content of start doc.
        ## What about past scans?
        folder = experiment_folder(bmm_catalog, self.uidlist[0])
        self.folder = folder
            
        ## test if XAS data file can be found
        if XDI['_user']['filename'] is None or XDI["_user"]["start"] is None:
            log_entry(logger, '*** Filename and/or start number not given.  (xafs_dossier).')
            return None
        firstfile = f'{XDI["_user"]["filename"]}.{XDI["_user"]["start"]:03d}'
        if not os.path.isfile(os.path.join(folder, firstfile)):
            log_entry(logger, f'*** Could not find {os.path.join(folder, firstfile)}')
            return None

        ## determine names of output dossier files
        basename     = XDI['_user']['filename']
        htmlfilename = os.path.join(folder, 'dossier/', XDI['_user']['filename']+'-01.html')
        if self.force_seqnumber is not None:
            seqnumber = self.force_seqnumber
        else:
            seqnumber = 1
            if os.path.isfile(htmlfilename):
                seqnumber = 2
                while os.path.isfile(os.path.join(folder, 'dossier', "%s-%2.2d.html" % (XDI['_user']['filename'],seqnumber))):
                    seqnumber += 1
        basename     = "%s-%2.2d" % (XDI['_user']['filename'],seqnumber)
        htmlfilename = os.path.join(folder, 'dossier', "%s-%2.2d.html" % (XDI['_user']['filename'],seqnumber))
        rkvs.set('BMM:dossier:seqnumber', seqnumber)
            
        ## sanity check the "report ID" (used to link to correct position in messagelog.html
        if self.rid is None: self.rid=''

        try:
            # dossier header
            with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'dossier_top.tmpl')) as f:
                content = f.readlines()
            thiscontent = ''.join(content).format(measurement   = 'XAFS',
                                                  filename      = XDI['_user']['filename'],
                                                  date          = XDI['_user']['startdate'],
                                                  rid           = self.rid,
                                                  seqnumber     = seqnumber, )

            # left sidebar, entry for XRF file in the case of fluorescence data
            thismode = self.plotting_mode(XDI['_user']['mode'])
            if thismode in ('xs', 'xs1', 'fluorescence') and '_xrffile' in XDI:
                with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'dossier_xrf_file.tmpl')) as f:
                    content = f.readlines()
                thiscontent += ''.join(content).format(basename      = basename,
                                                       xrffile       = quote('../XRF/'+XDI['_xrffile']),
                                                       xrfuid        = snapshots['xrf_uid'], )

            # # left sidebar, entry for HDF5 containing the Pilatus image stack    
            # if 'pilatus' in XDI['_user']['mode']:
            #     docs = bmm_catalog[self.uidlist[0]].documents()
            #     for d in docs:
            #         if d[0] == 'resource':
            #             this = os.path.join(d[1]['root'], d[1]['resource_path'])
            #             if '_%d' in this or re.search('%\d\.\dd', this) is not None:
            #                 this = this % 0
            #             if 'pilatus100k' in this:
            #                 pilatus_hdf5 = this
            #                 break
            #     with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'dossier_pilatus.tmpl')) as f:
            #         content = f.readlines()
            #     thiscontent += ''.join(content).format(hdf5file = pilatus_hdf5,
            #                                            basename = os.path.basename(pilatus_hdf5), )

                
            # middle part of dossier
            instrument = XDI['_user']['instrument']
            if instrument is None or instrument == '':
                instrument = self.instrument_default()
            with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'dossier_middle.tmpl')) as f:
                content = f.readlines()
            thiscontent += ''.join(content).format(basename      = XDI['_user']['filename'], # awkward, wants un-numbered basename
                                                   scanlist      = self.generate_scanlist(bmm_catalog),  # uses self.uidlist
                                                   motors        = self.motor_sidebar(bmm_catalog),
                                                   sample        = XDI['Sample']['name'],
                                                   prep          = XDI['Sample']['prep'],
                                                   comment       = XDI['_user']['comment'],
                                                   instrument    = instrument,
                                                   seqnumber     = seqnumber)
            

            # middle part, cameras, one at a time and only if actually snapped
            if 'webcam_uid' in snapshots and snapshots['webcam_uid'] != '':
                with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'dossier_img.tmpl')) as f:
                    content = f.readlines()
                thiscontent += ''.join(content).format(snap        = quote('../snapshots/'+snapshots['webcam_file']),
                                                       uid         = snapshots['webcam_uid'],
                                                       camera      = 'webcam',
                                                       description = 'XAS web camera', )
            if 'anacam_uid' in snapshots and snapshots['anacam_uid'] != '':
                with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'dossier_img.tmpl')) as f:
                    content = f.readlines()
                thiscontent += ''.join(content).format(snap        = quote('../snapshots/'+snapshots['analog_file']),
                                                       uid         = snapshots['anacam_uid'],
                                                       camera      = 'anacam',
                                                       description = 'analog pinhole camera', )
            if 'usbcam1_uid' in snapshots and snapshots['usbcam1_uid'] != '':
                with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'dossier_img.tmpl')) as f:
                    content = f.readlines()
                thiscontent += ''.join(content).format(snap        = quote('../snapshots/'+snapshots['usb1_file']),
                                                       uid         = snapshots['usbcam1_uid'],
                                                       camera      = 'usbcam1',
                                                       description = 'USB camera #1', )
            if 'usbcam2_uid' in snapshots and snapshots['usbcam2_uid'] != '':
                with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'dossier_img.tmpl')) as f:
                    content = f.readlines()
                thiscontent += ''.join(content).format(snap        = quote('../snapshots/'+snapshots['usb2_file']),
                                                       uid         = snapshots['usbcam2_uid'],
                                                       camera      = 'usb2cam',
                                                       description = 'USB camera #2', )
            if 'cam8_uid' in snapshots and snapshots['cam8_uid'] != '':
                with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'dossier_img.tmpl')) as f:
                    content = f.readlines()
                thiscontent += ''.join(content).format(snap        = quote('../snapshots/'+snapshots['cam8_file']),
                                                       uid         = snapshots['cam8_uid'],
                                                       camera      = 'cam8',
                                                       description = 'GigE camera #8', )
            if 'cam9_uid' in snapshots and snapshots['cam9_uid'] != '':
                with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'dossier_img.tmpl')) as f:
                    content = f.readlines()
                thiscontent += ''.join(content).format(snap        = quote('../snapshots/'+snapshots['cam9_file']),
                                                       uid         = snapshots['cam9_uid'],
                                                       camera      = 'cam9',
                                                       description = 'GigE camera #9', )
            
            # middle part, XRF and glancing angle alignment images
            if thismode in ('xs', 'xs1', 'fluorescence'):
                el = XDI['Element']['symbol']
                if 'xrf_uid' in XDI['_snapshots']:
                    if '4-element SDD' in bmm_catalog[self.uidlist[0]].start['detectors']:
                        rois = [int(bmm_catalog[snapshots['xrf_uid']].primary.data[el+'1'][0]),
                                int(bmm_catalog[snapshots['xrf_uid']].primary.data[el+'2'][0]),
                                int(bmm_catalog[snapshots['xrf_uid']].primary.data[el+'3'][0]),
                                int(bmm_catalog[snapshots['xrf_uid']].primary.data[el+'4'][0])]
                        ocrs = [int(numpy.array(bmm_catalog[snapshots['xrf_uid']].primary.data['4-element SDD_channel01_xrf']).sum()),
                                int(numpy.array(bmm_catalog[snapshots['xrf_uid']].primary.data['4-element SDD_channel02_xrf']).sum()),
                                int(numpy.array(bmm_catalog[snapshots['xrf_uid']].primary.data['4-element SDD_channel03_xrf']).sum()),
                                int(numpy.array(bmm_catalog[snapshots['xrf_uid']].primary.data['4-element SDD_channel04_xrf']).sum()) ]
                    elif '1-element SDD' in bmm_catalog[self.uidlist[0]].start['detectors']:
                        rois = [int(bmm_catalog[snapshots['xrf_uid']].primary.data[el+'8'][0]),]
                        ocrs = [int(numpy.array(bmm_catalog[snapshots['xrf_uid']].primary.data['1-element SDD_channel08_xrf']).sum()),]
                    elif '7-element SDD' in bmm_catalog[self.uidlist[0]].start['detectors']:
                        rois = [int(bmm_catalog[snapshots['xrf_uid']].primary.data[el+'1'][0]),
                                int(bmm_catalog[snapshots['xrf_uid']].primary.data[el+'2'][0]),
                                int(bmm_catalog[snapshots['xrf_uid']].primary.data[el+'3'][0]),
                                int(bmm_catalog[snapshots['xrf_uid']].primary.data[el+'4'][0]),
                                int(bmm_catalog[snapshots['xrf_uid']].primary.data[el+'5'][0]),
                                int(bmm_catalog[snapshots['xrf_uid']].primary.data[el+'6'][0]),
                                int(bmm_catalog[snapshots['xrf_uid']].primary.data[el+'7'][0]) ]
                        ocrs = [int(numpy.array(bmm_catalog[snapshots['xrf_uid']].primary.data['7-element SDD_channel01_xrf']).sum()),
                                int(numpy.array(bmm_catalog[snapshots['xrf_uid']].primary.data['7-element SDD_channel02_xrf']).sum()),
                                int(numpy.array(bmm_catalog[snapshots['xrf_uid']].primary.data['7-element SDD_channel03_xrf']).sum()),
                                int(numpy.array(bmm_catalog[snapshots['xrf_uid']].primary.data['7-element SDD_channel04_xrf']).sum()),
                                int(numpy.array(bmm_catalog[snapshots['xrf_uid']].primary.data['7-element SDD_channel05_xrf']).sum()),
                                int(numpy.array(bmm_catalog[snapshots['xrf_uid']].primary.data['7-element SDD_channel06_xrf']).sum()),
                                int(numpy.array(bmm_catalog[snapshots['xrf_uid']].primary.data['7-element SDD_channel07_xrf']).sum()) ]

                    with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'dossier_xrf_image.tmpl')) as f:
                        content = f.readlines()
                    thiscontent += ''.join(content).format(xrfsnap   = quote('../XRF/'+snapshots['xrf_image']),
                                                           pccenergy = '%.1f' % XDI['_user']['pccenergy'],
                                                           ocrs      = ', '.join(map(str,ocrs)),
                                                           rois      = ', '.join(map(str,rois)),
                                                           symbol    = XDI['Element']['symbol'],)
                if 'ga_filename' in snapshots:
                    with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'dossier_ga.tmpl')) as f:
                        content = f.readlines()
                    thiscontent += ''.join(content).format(ga_align = quote('../snapshots/'+ snapshots['ga_filename']),
                                                           ga_yuid  = snapshots['ga_yuid'],
                                                           ga_puid  = snapshots['ga_pitchuid'],
                                                           ga_fuid  = snapshots['ga_fuid'], )

            # end of dossier
            with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'dossier_bottom.tmpl')) as f:
                content = f.readlines()
            if XDI['Element']['symbol'] in all_references:
                this_ref = all_references[XDI['Element']['symbol']][3]
            else:
                this_ref = 'none'

            thiscontent += ''.join(content).format(e0            = '%.1f' % edge_energy(XDI['Element']['symbol'], XDI['Element']['edge']),
                                                   edge          = XDI['Element']['edge'],
                                                   element       = self.element_text(XDI['Element']['symbol']),
                                                   mode          = XDI['_user']['mode'],
                                                   bounds        = ", ".join(map(str, XDI['_user']['bounds_given'])),
                                                   steps         = XDI['_user']['steps'],
                                                   times         = XDI['_user']['times'],
                                                   reference     = re.sub(r'(\d+)', r'<sub>\1</sub>', this_ref),
                                                   seqstart      = datetime.datetime.fromtimestamp(bmm_catalog[self.uidlist[0]].start['time']).strftime('%A, %B %d, %Y %I:%M %p'),
                                                   seqend        = datetime.datetime.fromtimestamp(bmm_catalog[self.uidlist[-1]].stop['time']).strftime('%A, %B %d, %Y %I:%M %p'),
                                                   mono          = self.mono_text(bmm_catalog),
                                                   pdsmode       = '%s  (%s)' % self.pdstext(bmm_catalog),
                                                   pccenergy     = '%.1f' % XDI['_user']['pccenergy'],
                                                   experimenters = XDI['Scan']['experimenters'],
                                                   gup           = XDI['Facility']['GUP'],
                                                   saf           = XDI['Facility']['SAF'],
                                                   cycle         = XDI['Facility']['cycle'],
                                                   url           = XDI['_user']['url'],
                                                   doi           = XDI['_user']['doi'],
                                                   cif           = XDI['_user']['cif'],
                                                   initext       = highlight(XDI['_user']['initext'], IniLexer(),    HtmlFormatter()),
                                                   clargs        = highlight(XDI['_user']['clargs'],  PythonLexer(), HtmlFormatter()),
                                                   filename      = XDI['_user']['filename'],)

            with open(htmlfilename, 'w') as o:
                o.write(thiscontent)

            log_entry(logger, f':ledger: wrote XAFS dossier: {htmlfilename}')
        except Exception as E:
            log_entry(logger, f':warning: failed to write dossier file {htmlfilename}\n' + str(E))
            traceback.print_exc()

        self.manifest_file = os.path.join(folder, 'dossier', 'MANIFEST')            
        manifest = open(self.manifest_file, 'a')
        manifest.write(f'xafs␣{htmlfilename}\n')
        manifest.close()
        self.write_manifest(scantype='XAFS', startdate=XDI['_user']['startdate'])


    def write_manifest(self, scantype='XAFS', startdate=''):
        '''Update the scan manifest and the corresponding static html file.'''
        with open(self.manifest_file) as f:
            lines = [line.rstrip('\n') for line in f]

        experimentlist = ''
        for l in lines:
            (scantype, fname) = l.split('␣')
            if not os.path.isfile(fname):
                continue
            this = os.path.basename(fname)
            experimentlist += f'<li>{scantype}: <a href="./{this}">{this}</a></li>\n'

        with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'manifest.tmpl')) as f:
            content = f.readlines()
        indexfile = os.path.join(self.folder, 'dossier', '00INDEX.html')
        o = open(indexfile, 'w')
        o.write(''.join(content).format(date           = startdate,
                                        experimentlist = experimentlist,))
        o.close()



    def plotting_mode(self, mode):
        '''Return a sane string to describe the plotting mode.'''
        mode = mode.lower()
        if mode in ('fluo+yield', 'yield'):
            return 'yield'

        elif mode in ('fluo+pilatus', 'pilatus'):
            return 'pilatus'

        elif mode == 'dante':
            return 'dante'

        elif any(x in mode for x in ('xs', 'fluo', 'flou', 'both')):
            return 'fluorescence'

        elif 'ref' in mode:
            return 'reference'

        elif mode == 'test':
            return 'test'

        else:
            return 'transmission'

        
    def element_text(self, element='Po'):
        '''Return a string describing the element.  Returns Po if not
        specified or not specified correctly.'''
        if Z_number(element) is None:
            return ''
        else:
            thistext  = f'{element} '
            thistext += f'(<a href="https://en.wikipedia.org/wiki/{element_name(element)}">'
            thistext += f'{element_name(element)}</a>, '
            thistext += f'Z={Z_number(element)})'
            return thistext
        
    def generate_scanlist(self, bmm_catalog):
        '''Generate the html text for the XAS scan side bar for the XAFS
        dossier from the uidlist.'''
        template = '''
<li>
  <a href="../{filename}.{ext:03d}"
     title="Click to see the text of {filename}.{ext:03d}">
         {printedname}.{ext:03d}
  </a>
  <br>&nbsp;&nbsp;
  <a href="javascript:void(0)"
     onclick="toggle_visibility(\'{filename}.{ext:03d}\');"
     title="This is the scan number for {filename}.{ext:03d}, click to show/hide its UID">
         #{scanid}
  </a>
  <div id="{filename}.{ext:03d}" style="display:none;"><small>{uid}</small></div>
  <br>&nbsp;&nbsp;'''
        hdf5template = '''<a href="javascript:void(0)"
     onclick="toggle_visibility(\'{filename}.{ext:03d}.h5\');"
     title="This is the HDF5 file associated with {filename}.{ext:03d}, click to show/hide the HDF5 filename">
         XSpress3 HDF5
  </a>
  <div id="{filename}.{ext:03d}.h5" style="display:none;"><small>{hdf5file}</small></div>
'''
        pilatustemplate = '''<br>&nbsp;&nbsp;
  <a href="javascript:void(0)"
     onclick="toggle_visibility(\'{basename}\');"
     title="This is the Pilatus HDF5 file containing the immage stack from {filename}.{ext:03d}, click to show/hide the HDF5 filename">
         Pilatus HDF5
  </a>
  <div id="{basename}" style="display:none;"><small>{hdf5file}</small></div>
'''
        
#        template = '<li><a href="../{filename}.{ext:03d}" title="Click to see the text of {filename}.{ext:03d}">{printedname}.{ext:03d}</a>&nbsp;&nbsp;&nbsp;&nbsp;<a href="javascript:void(0)" onclick="toggle_visibility(\'{filename}.{ext:03d}\');" title="This is the scan number for {filename}.{ext:03d}, click to show/hide its UID">#{scanid}</a><div id="{filename}.{ext:03d}" style="display:none;"><small>{uid}</small></div></li>\n'

        text = ''
        for i,u in enumerate(self.uidlist):
            filename = bmm_catalog[u].start['XDI']['_user']['filename']
            ext = bmm_catalog[u].start['XDI']['_user']['start'] + i
            printedname = filename
            hdf5file = self.hdf5_filename(bmm_catalog, u)
            pilatusfile = self.pilatus_filename(bmm_catalog, u)
            if len(filename) > 11:
                printedname = filename[0:6] + '&middot;&middot;&middot;' + filename[-5:]
            text += template.format(filename    = filename,
                                    printedname = printedname,
                                    ext         = ext,
                                    scanid      = bmm_catalog[u].start['scan_id'],
                                    uid         = u,)
            if hdf5file is not None:
                text += hdf5template.format(filename    = filename,
                                            ext         = ext,
                                            hdf5file    = hdf5file,)
            if pilatusfile is not None:
                text += pilatustemplate.format(basename    = os.path.basename(pilatusfile),
                                               filename    = filename,
                                               ext         = ext,
                                               hdf5file    = pilatusfile,)
            ext = ext + 1
            text += '</li>\n'
        return text

    def mono_text(self, bmm_catalog):
        '''Text explaining the monochromator used for the measurement.  This
        is computed from motor values in the baseline.'''
        dcmx = bmm_catalog[self.uidlist[0]].baseline.data['dcm_x'][0]
        if dcmx > 10:
            return 'Si(311)'
        elif bmm_catalog[self.uidlist[0]].start['XDI']['_user']['ththth'] is True:
            return 'Si(333)'
        else:
            return 'Si(111)'


    def pdstext(self, bmm_catalog):
        '''Text explaining the photon delivery mode used for the measurement.
        This is computed from motor values in the baseline.'''
        m2v = bmm_catalog[self.uidlist[0]].baseline.data['m2_vertical'][0]
        m2p = bmm_catalog[self.uidlist[0]].baseline.data['m2_pitch'][0]
        m3p = bmm_catalog[self.uidlist[0]].baseline.data['m3_pitch'][0]
        m3v = bmm_catalog[self.uidlist[0]].baseline.data['m3_vertical'][0]
        m3l = bmm_catalog[self.uidlist[0]].baseline.data['m3_lateral'][0]
        if m2v < 0: # this is a focused mode
            if m2p > 3:
                return ('XRD', 'focused at goniometer, >8 keV')
            else:
                if m3v > -2:
                    return ('A', 'focused, >8 keV')
                elif m3v > -7:
                    return ('B', 'focused, <6 keV')
                else:
                    return ('C', 'focused, 6 to 8 keV')
        else:
            if m3p < 3:
                return ('F', 'unfocused, <6 keV')
            elif m3l > 0:
                return ('D', 'unfocused, >8 keV')
            else:
                return ('E', 'unfocused, 6 to 8 keV')


            
        

    def wheel_slot(self, value):
        '''Return the current slot number for a sample wheel computed from the
        xafs_wheel motor position.'''
        slotone = -30
        angle = round(value)
        this = round((-1*slotone-15+angle) / (-15)) % 24
        if this == 0: this = 24
        return this

    def spinner(self, pos):
        '''Return the current spinner number as an integer computed as the
        xafs_garot motor position.'''
        cur = pos % 360
        here = (9-round(cur/45)) % 8
        if here == 0:
            here = 8
        return here


    def hdf5_filename(self, bmm_catalog, uid):
        '''Find the path/name of the asset file associated with UID
        '''
        for d in bmm_catalog[uid].documents():
            if d[0] == 'resource':
                this = os.path.join(d[1]['root'], d[1]['resource_path'])
                if '_%d' in this:
                    this = this % 0
                if 'xspress3' in this:
                    return this 
        return None

    def pilatus_filename(self, bmm_catalog, uid):
        '''Find the path/name of the asset file associated with UID
        '''
        for d in bmm_catalog[uid].documents():
            if d[0] == 'resource':
                this = os.path.join(d[1]['root'], d[1]['resource_path'])
                if '_%d' in this:
                    this = this % 0
                if 'pilatus100k' in this:
                    return this
        return None

    def motor_entry(self, baseline, motor):
        '''Return a string for a single entry in the motor position sidebar.

        arguments
        =========
        baseline
          baseline stream of the current record

        motor (str)
          name of the current motor as a string
        '''
        if motor == 'slot':     # deal specially with major instruments
            motor_name = motor
            try:
                position = self.wheel_slot(float(baseline["xafs_wheel"][0]))
                text = f'              <div>{motor}, {position}</div>\n'
            except:
                text = f'              <div>{motor}, ---</div>\n'
        elif motor == 'spinner':
            motor_name = motor
            try:
                position = self.spinner(float(baseline["xafs_garot"][0]))
                text = f'              <div>{motor}, {position}</div>\n'
            except:
                text = f'              <div>{motor}, ---</div>\n'
        else:                   # 'xafs_table' is a long string, special case
            motor_name = motor.replace('xt', 'xafs_table')
            label = motor
            if any(motor.startswith(x) for x in ('slits2', 'slits3', 'xt', 'm2', 'm3')):
                label = motor.replace('_', '.')
            if motor_name in baseline:  # deal elegantly if a motor is not listed in the baseline
                position = baseline[motor_name][0]
                text = f'              <div>{label}, {position:.3f}</div>\n'
            else:
                text = ''
        return text

    def motor_paragrph(self, baseline, title, motorlist):
        '''Format a labeled group of motors for the motor position sidebar.
        '''
        content = ''
        tmpl = '''
            <span class="motorheading">{title}:</span>
            <div id="motorgrid">
{content}            </div>
'''
        for m in motorlist:
            content += self. motor_entry(baseline, m)
        text = tmpl.format(title = title,
                           content = content)
        return text
    
    def motor_sidebar_new(self, bmm_catalog):
        '''Generate a list of motor positions for the sidebar of the static
        html page for a scan sequence.  Return value is a long string
        with html tags and entities embedded in the string.

        All motor positions are taken from the first entry in the
        record's baseline stream.

        Parameters
        ----------
        bmm_catalog : Tiled catalog
            catalog in which to find the record for a UID string

        >>> text = dossier.motor_sidebar(bmm_catalog)

        '''
        baseline = bmm_catalog[self.uidlist[0]].baseline.read()

        motors = ''
        
        motorlist = ('xafs_x', 'xafs_y', 'xafs_pitch', 'xafs_roll', 'xafs_wheel', 'xafs_garot',
                     'xafs_ref', 'xafs_refx', 'xafs_refy',
                     'xafs_detx', 'xafs_dety', 'xafs_detz')
        motors += self.motor_paragrph(baseline, 'XAFS stages', motorlist)
        
        motorlist = ('slot', 'spinner', 'dm3_bct')
        motors += self.motor_paragrph(baseline, 'Instruments', motorlist)

        motorlist = ('slits3_vsize', 'slits3_vcenter', 'slits3_hsize', 'slits3_hcenter', 'slits3_top', 'slits3_bottom', 'slits3_inboard', 'slits3_outboard')
        motors += self.motor_paragrph(baseline, 'Slits3', motorlist)

        motorlist = ('m2_vertical', 'm2_lateral', 'm2_pitch', 'm2_roll', 'm2_yaw', 'm2_yu', 'm2_ydo', 'm2_ydi', 'm2_xu', 'm2_xd', 'm2_bender')
        motors += self.motor_paragrph(baseline, 'M2', motorlist)
        
        stripe = '(Rh/Pt stripe)'
        if baseline['m3_xu'][0] < 0:
            stripe = '(Si stripe)'
        motorlist = ('m3_vertical', 'm3_lateral', 'm3_pitch', 'm3_roll', 'm3_yaw', 'm3_yu', 'm3_ydo', 'm3_ydi', 'm3_xu', 'm3_xd')
        motors += self.motor_paragrph(baseline, f'M3 {stripe}', motorlist)

        motorlist = ('xt_vertical', 'xt_pitch', 'xt_roll', 'xt_yu', 'xt_ydo', 'xt_ydi')
        motors += self.motor_paragrph(baseline, 'XAFS table', motorlist)

        motorlist = ('slits2_vsize', 'slits2_vcenter', 'slits2_hsize', 'slits2_hcenter', 'slits2_top', 'slits2_bottom', 'slits2_inboard', 'slits2_outboard')
        motors += self.motor_paragrph(baseline, 'Slits2', motorlist)

        motorlist = ('dcm_x', 'dcm_pitch', 'dcm_roll')
        motors += self.motor_paragrph(baseline, 'Monochromator', motorlist)

        motorlist = ('dm3_foils', 'dm2_fs')
        motors += self.motor_paragrph(baseline, 'Diagnostics', motorlist)

        return motors


    def motor_sidebar_orig(self, bmm_catalog):
        '''Generate a list of motor positions for the sidebar of the static
        html page for a scan sequence.  Return value is a long string
        with html tags and entities embedded in the string.

        All motor positions are taken from the first entry in the
        record's baseline stream.

        Parameters
        ----------
        bmm_catalog : Tiled catalog
            catalog in which to find the record for a UID string

        >>> text = dossier.motor_sidebar(bmm_catalog)

        '''
        baseline = bmm_catalog[self.uidlist[0]].baseline.read()

        motors = ''

        motors +=  '<span class="motorheading">XAFS stages:</span>\n'
        motors +=  '            <div id="motorgrid">\n'
        motors += f'              <div>xafs_x, {baseline["xafs_x"][0]:.3f}</div>\n'
        motors += f'              <div>xafs_y, {baseline["xafs_y"][0]:.3f}</div>\n'
        motors += f'              <div>xafs_pitch, {baseline["xafs_pitch"][0]:.3f}</div>\n'
        motors += f'              <div>xafs_roll, {baseline["xafs_roll"][0]:.3f}</div>\n'
        motors += f'              <div>xafs_wheel, {baseline["xafs_wheel"][0]:.3f}</div>\n'
        motors += f'              <div>xafs_garot, {baseline["xafs_garot"][0]:.3f}</div>\n'
        motors += f'              <div>xafs_ref, {baseline["xafs_ref"][0]:.3f}</div>\n'
        motors += f'              <div>xafs_refx, {baseline["xafs_refx"][0]:.3f}</div>\n'
        motors += f'              <div>xafs_refy, {baseline["xafs_refy"][0]:.3f}</div>\n'
        motors += f'              <div>xafs_detx, {baseline["xafs_detx"][0]:.3f}</div>\n'
        motors += f'              <div>xafs_dety, {baseline["xafs_detx"][0]:.3f}</div>\n'
        motors += f'              <div>xafs_detz, {baseline["xafs_detx"][0]:.3f}</div>\n'
        motors +=  '            </div>\n'

        motors +=  '            <span class="motorheading">Instruments:</span>\n'
        motors +=  '            <div id="motorgrid">\n'
        motors += f'              <div>slot, {self.wheel_slot(float(baseline["xafs_wheel"][0]))}</div>\n'
        motors += f'              <div>spinner, {self.spinner(float(baseline["xafs_garot"][0]))}</div>\n'
        motors += f'              <div>dm3_bct, {baseline["dm3_bct"][0]:.3f}</div>\n'
        motors +=  '            </div>\n'

        motors +=  '            <span class="motorheading">Slits3:</span>\n'
        motors +=  '            <div id="motorgrid">\n'
        motors += f'              <div>slits3_vsize, {baseline["slits3_vsize"][0]:.3f}</div>\n'
        motors += f'              <div>slits3_vcenter, {baseline["slits3_vcenter"][0]:.3f}</div>\n'
        motors += f'              <div>slits3_hsize, {baseline["slits3_hsize"][0]:.3f}</div>\n'
        motors += f'              <div>slits3_hcenter, {baseline["slits3_hcenter"][0]:.3f}</div>\n'
        motors += f'              <div>slits3_top, {baseline["slits3_top"][0]:.3f}</div>\n'
        motors += f'              <div>slits3_bottom, {baseline["slits3_bottom"][0]:.3f}</div>\n'
        motors += f'              <div>slits3_inboard, {baseline["slits3_inboard"][0]:.3f}</div>\n'
        motors += f'              <div>slits3_outboard, {baseline["slits3_outboard"][0]:.3f}</div>\n'
        motors +=  '            </div>\n'

        motors +=  '            <span class="motorheading">M2:</span>\n'
        motors +=  '            <div id="motorgrid">\n'
        motors += f'              <div>m2_vertical, {baseline["m2_vertical"][0]:.3f}</div>\n'
        motors += f'              <div>m2_lateral, {baseline["m2_lateral"][0]:.3f}</div>\n'
        motors += f'              <div>m2_pitch, {baseline["m2_pitch"][0]:.3f}</div>\n'
        motors += f'              <div>m2_roll, {baseline["m2_roll"][0]:.3f}</div>\n'
        motors += f'              <div>m2_yaw, {baseline["m2_yaw"][0]:.3f}</div>\n'
        motors += f'              <div>m2_yu, {baseline["m2_yu"][0]:.3f}</div>\n'
        motors += f'              <div>m2_ydo, {baseline["m2_ydo"][0]:.3f}</div>\n'
        motors += f'              <div>m2_ydi, {baseline["m2_ydi"][0]:.3f}</div>\n'
        motors += f'              <div>m2_xu, {baseline["m2_xu"][0]:.3f}</div>\n'
        motors += f'              <div>m2_xd, {baseline["m2_xd"][0]:.3f}</div>\n'
        motors += f'              <div>m2_bender, {baseline["m2_bender"][0]:.3f}</div>\n'
        motors +=  '            </div>\n'

        stripe = '(Rh/Pt stripe)'
        if baseline['m3_xu'][0] < 0:
            stripe = '(Si stripe)'
        motors += f'            <span class="motorheading">M3 {stripe}:</span>\n'
        motors +=  '            <div id="motorgrid">\n'
        motors += f'              <div>m3_vertical, {baseline["m3_vertical"][0]:.3f}</div>\n'
        motors += f'              <div>m3_lateral, {baseline["m3_lateral"][0]:.3f}</div>\n'
        motors += f'              <div>m3_pitch, {baseline["m3_pitch"][0]:.3f}</div>\n'
        motors += f'              <div>m3_roll, {baseline["m3_roll"][0]:.3f}</div>\n'
        motors += f'              <div>m3_yaw, {baseline["m3_yaw"][0]:.3f}</div>\n'
        motors += f'              <div>m3_yu, {baseline["m3_yu"][0]:.3f}</div>\n'
        motors += f'              <div>m3_ydo, {baseline["m3_ydo"][0]:.3f}</div>\n'
        motors += f'              <div>m3_ydi, {baseline["m3_ydi"][0]:.3f}</div>\n'
        motors += f'              <div>m3_xu, {baseline["m3_xu"][0]:.3f}</div>\n'
        motors += f'              <div>m3_xd, {baseline["m3_xd"][0]:.3f}</div>\n'
        motors +=  '            </div>\n'

        motors +=  '            <span class="motorheading">XAFS table:</span>\n'
        motors +=  '            <div id="motorgrid">\n'
        motors += f'              <div>xt_vertical, {baseline["xafs_table_vertical"][0]:.3f}</div>\n'
        motors += f'              <div>xt_pitch, {baseline["xafs_table_pitch"][0]:.3f}</div>\n'
        motors += f'              <div>xt_roll, {baseline["xafs_table_roll"][0]:.3f}</div>\n'
        motors += f'              <div>xt_yu, {baseline["xafs_table_yu"][0]:.3f}</div>\n'
        motors += f'              <div>xt_ydo, {baseline["xafs_table_ydo"][0]:.3f}</div>\n'
        motors += f'              <div>xt_ydi, {baseline["xafs_table_ydi"][0]:.3f}</div>\n'
        motors +=  '            </div>\n'


        motors +=  '            <span class="motorheading">Slits2:</span>\n'
        motors +=  '            <div id="motorgrid">\n'
        motors += f'              <div>slits2_vsize, {baseline["slits2_vsize"][0]:.3f}</div>\n'
        motors += f'              <div>slits2_vcenter, {baseline["slits2_vcenter"][0]:.3f}</div>\n'
        motors += f'              <div>slits2_hsize, {baseline["slits2_hsize"][0]:.3f}</div>\n'
        motors += f'              <div>slits2_hcenter, {baseline["slits2_hcenter"][0]:.3f}</div>\n'
        motors += f'              <div>slits2_top, {baseline["slits2_top"][0]:.3f}</div>\n'
        motors += f'              <div>slits2_bottom, {baseline["slits2_bottom"][0]:.3f}</div>\n'
        motors += f'              <div>slits2_inboard, {baseline["slits2_inboard"][0]:.3f}</div>\n'
        motors += f'              <div>slits2_outboard, {baseline["slits2_outboard"][0]:.3f}</div>\n'
        motors +=  '            </div>\n'

        motors +=  '            <span class="motorheading">Monochromator:</span>\n'
        motors +=  '            <div id="motorgrid">\n'
        motors += f'              <div>dcm_x, {baseline["dcm_x"][0]:.3f}</div>\n'
        motors += f'              <div>dcm_pitch, {baseline["dcm_pitch"][0]:.3f}</div>\n'
        motors += f'              <div>dcm_roll, {baseline["dcm_roll"][0]:.3f}</div>\n'
        motors +=  '            </div>\n'
        
        motors +=  '            <span class="motorheading">Diagnostics:</span>\n'
        motors +=  '            <div id="motorgrid">\n'
        motors += f'              <div>dm3_foils, {baseline["dm3_foils"][0]:.3f}</div>\n'
        motors += f'              <div>dm2_fs, {baseline["dm2_fs"][0]:.3f}</div>\n'
        motors +=  '            </div>\n'
        
        return motors



    def motor_sidebar(self, bmm_catalog):
        motors = self.motor_sidebar_new(bmm_catalog)
        return motors
    
    def instrument_default(self):
        thistext  =  '''
	    <div>
	      <h3>Instrument</h3>
	      <ul>
               <li>(no instrument information)</li>
	      </ul>
	    </div>
'''
        return thistext



    def sead_instrument_entry(self, seadimage, seaduid):
        thistext  =  '      <div>\n'
        thistext +=  '        <h3>Instrument: SEAD scan</h3>\n'
        thistext += f'          <a href="../snapshots/{seadimage}">\n'
        thistext += f'                        <img src="../snapshots/{seadimage}" width="300" alt="" /></a>\n'
        thistext +=  '          <br>'
        thistext += f'          <a href="javascript:void(0)" onclick="toggle_visibility(\'areascan\');" title="Click to show/hide the UID of this areascan">(uid)</a><div id="areascan" style="display:none;"><small>{seaduid}</small></div>\n'
        thistext +=  '      </div>\n'
        return thistext

    
    def sead_dossier(self, catalog, logger):
        '''Write a dossier for a SEAD measurement. '''

        if len(self.uidlist) == 0:
            log_entry(logger, '*** cannot write SEAD dossier, uidlist is empty')
            return None

        ## gather information for the dossier from the start document
        ## of the first scan in the sequence
        startdoc = catalog[self.uidlist[0]].start
        XDI = startdoc['XDI']
        if '_snapshots' in XDI:
            snapshots = XDI['_snapshots']
        else:
            snapshots = {}
        

        folder = self.folder
        if folder is None or folder == '':
            folder = XDI["_user"]["folder"]
        ## determine folder from content of start doc.
        ## What about past scans?
        folder = experiment_folder(catalog, self.uidlist[0])
        self.folder = folder
            
        ## test if SEAD data file can be found
        if XDI['_user']['filename'] is None or XDI["_user"]["start"] is None:
            log_entry(logger, '*** Filename and/or start number not given.  (sead_dossier).')
            return None
        firstfile = f'{XDI["_user"]["filename"]}.{XDI["_user"]["start"]:03d}'
        if not os.path.isfile(os.path.join(folder, firstfile)):
            log_entry(logger, f'*** Could not find {os.path.join(folder, firstfile)}')
            return None


        ## determine names of output dossier files
        basename     = XDI['_user']['filename']
        htmlfilename = os.path.join(folder, 'dossier/', XDI['_user']['filename']+'-01.html')
        seqnumber = 1
        if os.path.isfile(htmlfilename):
            seqnumber = 2
            while os.path.isfile(os.path.join(folder, 'dossier', "%s-%2.2d.html" % (XDI['_user']['filename'],seqnumber))):
                seqnumber += 1
            basename     = "%s-%2.2d" % (XDI['_user']['filename'],seqnumber)
            htmlfilename = os.path.join(folder, 'dossier', "%s-%2.2d.html" % (XDI['_user']['filename'],seqnumber))


        with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'sead_dossier.tmpl')) as f:
                content = f.readlines()
        thiscontent = ''.join(content).format(measurement   = 'SEAD',
                                              filename      = XDI['_user']['filename'],
                                              sead          = os.path.basename(XDI['_filename']),
                                              date          = XDI['_user']['startdate'],
                                              seqnumber     = seqnumber,  # need to replicate from above
                                              rid           = self.rid,
                                              energy        = f'{XDI["Scan"]["edge_energy"]:1f}',
                                              edge          = f'{XDI["Element"]["edge"]}',
                                              element       = self.element_text(XDI['Element']['symbol']),
                                              sample        = XDI['Sample']['name'],
                                              prep          = XDI['Sample']['prep'],
                                              comment       = XDI['_user']['comment'],
                                              instrument    = self.sead_instrument_entry(XDI['_user']['pngfile'], self.uidlist[0]),
                                              npoints       = len(catalog[self.uidlist[0]].primary.data["time"]),
                                              dwell         = XDI['Scan']['dwell_time'],
                                              delay         = XDI['Scan']['delay'],
                                              shutter       = XDI['_user']['shutter'],
                                              websnap       = quote('../snapshots/'+snapshots['webcam_file']),
                                              webuid        = snapshots['webcam_uid'],
                                              anasnap       = quote('../snapshots/'+snapshots['analog_file']),
                                              anauid        = snapshots['anacam_uid'],
                                              usb1snap      = quote('../snapshots/'+snapshots['usb1_file']),
                                              usb1uid       = snapshots['usbcam1_uid'],
                                              usb2snap      = quote('../snapshots/'+snapshots['usb2_file']),
                                              usb2uid       = snapshots['usbcam2_uid'],
                                              cam8snap      = quote('../snapshots/'+snapshots['cam8_file']),
                                              cam8uid       = snapshots['cam8_uid'],
                                              cam9snap      = quote('../snapshots/'+snapshots['cam9_file']),
                                              cam9uid       = snapshots['cam9_uid'],
                                              mode          = XDI['_user']['mode'],
                                              motors        = self.motor_sidebar(catalog),
                                              seqstart      = datetime.datetime.fromtimestamp(catalog[self.uidlist[0]].start['time']).strftime('%A, %B %d, %Y %I:%M %p'),
                                              seqend        = datetime.datetime.fromtimestamp(catalog[self.uidlist[-1]].stop['time']).strftime('%A, %B %d, %Y %I:%M %p'),
                                              mono          = self.mono_text(catalog),
                                              pdsmode       = '%s  (%s)' % self.pdstext(catalog),
                                              experimenters = XDI['_user']['experimenters'],
                                              gup           = XDI['Facility']['GUP'],
                                              saf           = XDI['Facility']['SAF'],
                                              url           = XDI['_user']['url'],
                                              doi           = XDI['_user']['doi'],
                                              cif           = XDI['_user']['cif'],
                                              initext       = highlight(XDI['_user']['initext'], IniLexer(), HtmlFormatter()),
                                              clargs        = highlight(XDI['_user']['clargs'], PythonLexer(), HtmlFormatter()),
                                              hdf5file      = self.hdf5_filename(catalog, self.uidlist[0]),
        )
        with open(htmlfilename, 'a') as o:
            o.write(thiscontent)

        log_entry(logger, f'wrote SEAD dossier: {htmlfilename}')

        self.manifest_file = os.path.join(folder, 'dossier', 'MANIFEST')            
        manifest = open(self.manifest_file, 'a')
        manifest.write(f'sead␣{htmlfilename}\n')
        manifest.close()
        self.write_manifest(scantype='SEAD', startdate=XDI['_user']['startdate'])





    def raster_instrument_entry(self, rasterimage, rasteruid):
        thistext  =  '      <div>\n'
        thistext +=  '        <h3>Instrument: Raster scan</h3>\n'
        thistext += f'          <a href="../maps/{rasterimage}">\n'
        thistext += f'                        <img src="../maps/{rasterimage}" width="300" alt="" /></a>\n'
        thistext +=  '          <br>'
        thistext += f'          <a href="javascript:void(0)" onclick="toggle_visibility(\'areascan\');" title="Click to show/hide the UID of this areascan">(uid)</a><div id="areascan" style="display:none;"><small>{rasteruid}</small></div>\n'
        thistext +=  '      </div>\n'
        return thistext

    def raster_dossier(self, catalog, logger):
        '''Write a dossier for a raster measurement. '''

        if len(self.uidlist) == 0:
            log_entry(logger, '*** cannot write dossier, uidlist is empty')
            return None

        ## gather information for the dossier from the start document
        ## of the first scan in the sequence
        startdoc = catalog[self.uidlist[0]].start
        XDI = startdoc['XDI']
        if '_snapshots' in XDI:
            snapshots = XDI['_snapshots']
        else:
            snapshots = {}


        folder = self.folder
        if folder is None or folder == '':
            folder = XDI["_user"]["folder"]
        ## determine folder from content of start doc.
        ## What about past scans?
        folder = experiment_folder(catalog, self.uidlist[0])
        self.folder = folder
            

        ## check for filename stub
        if XDI['_user']['filename'] is None:
            log_entry(logger, '*** Filename not given.  (raster_dossier).')
            return None


        ## determine names of output dossier files
        basename     = XDI['_user']['filename']
        htmlfilename = os.path.join(folder, 'dossier/', XDI['_user']['filename']+'-01.html')
        seqnumber = 1
        if os.path.isfile(htmlfilename):
            seqnumber = 2
            while os.path.isfile(os.path.join(folder, 'dossier', "%s-%2.2d.html" % (XDI['_user']['filename'],seqnumber))):
                seqnumber += 1
            basename     = "%s-%2.2d" % (XDI['_user']['filename'],seqnumber)
            htmlfilename = os.path.join(folder, 'dossier', "%s-%2.2d.html" % (XDI['_user']['filename'],seqnumber))

        with open(os.path.join(startup_dir, 'consumer', 'tmpl', 'raster_dossier.tmpl')) as f:
                content = f.readlines()
        thiscontent = ''.join(content).format(measurement   = 'RASTER',
                                              filename      = XDI['_user']['filename'],
                                              basename      = basename,
                                              date          = XDI['_user']['startdate'],
                                              seqnumber     = seqnumber,
                                              rid           = self.rid,
                                              energy        = f'{XDI["Beamline"]["energy"]:1f}',
                                              edge          = f'{XDI["Element"]["edge"]}',
                                              element       = self.element_text(XDI['Element']['symbol']),
                                              sample        = XDI['Sample']['name'],
                                              prep          = XDI['Sample']['prep'],
                                              comment       = XDI['_user']['comment'],
                                              instrument    = self.raster_instrument_entry(XDI['_snapshots']['pngout'], self.uidlist[0]),
                                              fast_motor    = XDI['_user']['fast_motor'],
                                              slow_motor    = XDI['_user']['slow_motor'],
                                              fast_init     = XDI['_user']['fast_init'],
                                              slow_init     = XDI['_user']['slow_init'],
                                              websnap       = quote('../snapshots/'+snapshots['webcam_file']),
                                              webuid        = snapshots['webcam_uid'],
                                              anasnap       = quote('../snapshots/'+snapshots['analog_file']),
                                              anauid        = snapshots['anacam_uid'],
                                              usb1snap      = quote('../snapshots/'+snapshots['usb1_file']),
                                              usb1uid       = snapshots['usbcam1_uid'],
                                              usb2snap      = quote('../snapshots/'+snapshots['usb2_file']),
                                              usb2uid       = snapshots['usbcam2_uid'],
                                              cam8snap      = quote('../snapshots/'+snapshots['cam8_file']),
                                              cam8uid       = snapshots['cam8_uid'],
                                              cam9snap      = quote('../snapshots/'+snapshots['cam9_file']),
                                              cam9uid       = snapshots['cam9_uid'],
                                              xlsxout       = XDI['_snapshots']['xlsxout'],
                                              matout        = XDI['_snapshots']['matout'],
                                              mode          = XDI['_user']['mode'],
                                              motors        = self.motor_sidebar(catalog),
                                              seqstart      = datetime.datetime.fromtimestamp(catalog[self.uidlist[0]].start['time']).strftime('%A, %B %d, %Y %I:%M %p'),
                                              seqend        = datetime.datetime.fromtimestamp(catalog[self.uidlist[-1]].stop['time']).strftime('%A, %B %d, %Y %I:%M %p'),
                                              mono          = self.mono_text(catalog),
                                              pdsmode       = '%s  (%s)' % self.pdstext(catalog),
                                              experimenters = XDI['_user']['experimenters'],
                                              gup           = XDI['Facility']['GUP'],
                                              saf           = XDI['Facility']['SAF'],
                                              url           = XDI['_user']['url'],
                                              doi           = XDI['_user']['doi'],
                                              cif           = XDI['_user']['cif'],
                                              initext       = highlight(XDI['_user']['initext'], IniLexer(), HtmlFormatter()),
                                              clargs        = highlight(XDI['_user']['clargs'], PythonLexer(), HtmlFormatter()),
                                              hdf5file      = self.hdf5_filename(catalog, self.uidlist[0]),
        )
        with open(htmlfilename, 'a') as o:
            o.write(thiscontent)

        log_entry(logger, f'wrote raster dossier: {htmlfilename}')

        self.manifest_file = os.path.join(folder, 'dossier', 'MANIFEST')            
        manifest = open(self.manifest_file, 'a')
        manifest.write(f'raster␣{htmlfilename}\n')
        manifest.close()
        self.write_manifest()


        

class XASFile():

    units = {
        # 'Detector': {'I0_dark'           : ('A', 5),
        #              'It_dark'           : ('A', 5),
        #              'Ir_dark'           : ('A', 5),
        # },
        'Facility': {'energy'            : ('geV', None),
                     'current'           : ('mA', 1),
        },
        'Mono':     {'d_spacing'         : ('Å', 7),
                     'encoder_resolution': ('degrees per step', None),
                     'angle_offset'      : ('degrees', 7),
        },
        'Sample':   {'x'                 : ('mm', 3),
                     'y'                 : ('mm', 3),
                     'pitch'             : ('mm', 3),
                     'roll'              : ('mm', 3),
                     'SDD_position'      : ('mm', 1),
        },
    }
    
    def plot_hint(self, catalog=None, uid=None):
        text = 'ln(I0/It)  --  ln($5/$6)'
        el = catalog[uid].start['XDI']['Element']['symbol']
        
        if '1-element SDD' in catalog[uid].start['detectors']:
            text = f'{el}8/I0  --  $8/$5'
        elif '4-element SDD' in catalog[uid].start['detectors']:
            text = f'({el}1+{el}2+{el}3+{el}4)/I0  --  ($8+$9+$10+$11)/$5'
        elif '7-element SDD' in catalog[uid].start['detectors']:
            text = f'({el}1+{el}2+{el}3+{el}4+{el}5+{el}6+{el}7)/I0  --  ($8+$9+$10+$11+$12+$13+$14)/$5'
        elif 'reference' in catalog[uid].start['plan_name']:
            text = 'ln(It/Ir)  --  ln($6/$7)'
        elif 'yield' in catalog[uid].start['plan_name']:
            text = 'Iy/I0  --  $8/$5'
        elif 'pips' in catalog[uid].start['plan_name']:
            text = 'Pips/I0  --  $8/$5'
        elif 'test' in catalog[uid].start['plan_name']:
            text = 'I0  --  $5'
        return text

    # def file_resource(self, catalog, uid):
    #     docs = catalog[uid].documents()
    #     found = []
    #     for d in docs:
    #         if d[0] == 'resource':
    #             this = os.path.join(d[1]['root'], d[1]['resource_path'])
    #             if '_%d' in this or re.search('%\d\.\dd', this) is not None:
    #                 this = this % 0
    #             found.append(this)
    #     return found
    
    def to_xdi(self, catalog=None, uid=None, filename=None, logger=None, include_yield=False):
        '''Write an XDI-style file for an XAS scan.

        '''
        r = catalog[uid]
        metadata = r.metadata
        xdi = r.start["XDI"]
        if filename is None:
            filename = xdi['_filename']
        fname = os.path.join(experiment_folder(catalog, uid), filename)
        handle = open(fname, 'w')
        handle.write(f'# XDI/1.0 BlueSky/{bluesky_version} BMM/{pathlib.Path(sys.executable).parts[-3]}\n')

        ## header lines with metadata from the XDI dictionary
        for family in ('Beamline', 'Detector', 'Element', 'Facility', 'Mono', 'Sample', 'Scan'):
            for k in xdi[family].keys():
                unit, precision = '', None
                if family in self.units and k in self.units[family]:
                    unit, precision = self.units[family][k]  # add units if needed
                if family == 'Sample' and k == 'comment':
                    continue
                if family == 'Sample' and k == 'extra_metadata':
                    continue
                value = xdi[family][k] 
                if precision is not None:  # add precision if needed
                    value = round(float(value), precision)
                handle.write(f'# {family}.{k}: {value} {unit}\n')
        start = datetime.datetime.fromtimestamp(r.start['time']).strftime("%Y-%m-%dT%H:%M:%S") # '%A, %d %B, %Y %I:%M %p')
        end   = datetime.datetime.fromtimestamp(r.stop['time']).strftime("%Y-%m-%dT%H:%M:%S")  # '%A, %d %B, %Y %I:%M %p')
        handle.write(f'# Scan.start_time: {start}\n')
        handle.write(f'# Scan.end_time: {end}\n')
        handle.write(f'# Scan.uid: {uid}\n')
        handle.write(f'# Scan.transient_id: {metadata["start"]["scan_id"]}\n')
        
        if any(x in r.start['detectors'] for x in ('1-element SDD', '4-element SDD', '7-element SDD')):
            hdf5files = file_resource(catalog, uid)
            for h in hdf5files:
                relative = '/'.join(h.split('/')[-6:])
                if 'xspress3' in relative:
                    handle.write(f'# Scan.xspress3_hdf5_file: {relative}\n')
                elif 'pilatus' in relative:
                    handle.write(f'# Scan.pilatus100k_hdf5_file: {relative}\n')
        ## is this correct?  need to test....
            
        handle.write(f'# Scan.plot_hint: {self.plot_hint(catalog=catalog, uid=uid)}\n')
        handle.write( '# Column.1: energy eV\n')
        handle.write( '# Column.2: requested_energy eV\n')
        handle.write( '# Column.3: measurement_time seconds\n')
        handle.write( '# Column.4: xmu\n')
        handle.write( '# Column.5: I0 nA\n')
        handle.write( '# Column.6: Itrans nA\n')
        handle.write( '# Column.7: Irefer nA\n')

        ## Column.N header lines
        column_list = ['dcm_energy', 'dcm_energy_setpoint', 'dwti_dwell_time', 'I0', 'It', 'Ir']
        column_labels = ['energy', 'requested_energy', 'measurement_time', 'xmu', 'I0', 'It', 'Ir']
        if 'yield' in r.start['plan_name'] or include_yield is True:
            handle.write( '# Column.8: Iy nA\n')
            column_list.append('Iy')
            column_labels.append('Iy')

        if 'pips' in r.start['plan_name'].lower():
            handle.write( '# Column.8: PIPS nA\n')
            column_list.append('Pips')
            column_labels.append('Pips')

        
        el = r.start['XDI']['Element']['symbol']
        nchan = 0
        if '1-element SDD' in r.start['detectors']:
            nchan = 1
            column_list.append(f'{el}8')
            column_labels.append(f'{el}8')
            handle.write(f'# Column.8: {el}8 counts\n')
        elif '4-element SDD' in r.start['detectors']:
            column_list.extend([f'{el}1', f'{el}2', f'{el}3', f'{el}4'])
            column_labels.extend([f'{el}1', f'{el}2', f'{el}3', f'{el}4'])
            nchan = 4
            if 'pilatus100k-1' in r.start['detectors']:
                column_list.extend(['diffuse', 'specular'])
                column_labels.extend(['diffuse', 'specular'])
                
        elif '7-element SDD' in r.start['detectors']:
            column_list.extend([f'{el}1', f'{el}2', f'{el}3', f'{el}4', f'{el}5', f'{el}6', f'{el}7'])
            column_labels.extend([f'{el}1', f'{el}2', f'{el}3', f'{el}4', f'{el}5', f'{el}6', f'{el}7'])
            nchan = 7
            if 'pilatus100k-1' in r.start['detectors']:
                column_list.extend(['diffuse', 'specular'])
                column_labels.extend(['diffuse', 'specular'])
                
        if nchan > 0:
            if 'yield' in r.start['plan_name'] or include_yield is True:
                for i in range(1, nchan+1):
                    handle.write(f'# Column.{i+8}: {el}{i} counts\n')
            else:
                for i in range(1, nchan+1):
                    handle.write(f'# Column.{i+7}: {el}{i} counts\n')
        if 'pilatus100k-1' in r.start['detectors']:
            handle.write(f'# Column.{8+nchan}: diffuse counts\n')
            handle.write(f'# Column.{9+nchan}: specular counts\n')

                    
        ## prepare data table and insert xmu column
        xa = catalog[uid].primary.read(column_list)
        p = xa.to_pandas()
        column_list.insert(3, 'xmu')
        if '1-element SDD' in r.start['detectors']:
            p['xmu'] = p[f'{el}8']/p['I0']
        elif '4-element SDD' in r.start['detectors']:
            p['xmu'] = (p[f'{el}1']+p[f'{el}2']+p[f'{el}3']+p[f'{el}4'])/p['I0']
        elif '7-element SDD' in r.start['detectors']:
            p['xmu'] = (p[f'{el}1']+p[f'{el}2']+p[f'{el}3']+p[f'{el}4']+p[f'{el}5']+p[f'{el}6']+p[f'{el}7'])/p['I0']
        elif 'transmission' in r.start['plan_name']:
            p['xmu'] = numpy.log(p['It']/p['I0'])
        elif 'reference' in r.start['plan_name']:
            p['xmu'] = numpy.log(p['Ir']/p['It'])
        elif 'yield' in r.start['plan_name']:
            p['xmu'] = p['Iy']/p['It']
        elif 'pips' in r.start['plan_name']:
            p['xmu'] = p['Pips']/p['It']
        elif 'test' in r.start['plan_name']:
            p['xmu'] = p['I0']
        else:
            p['xmu'] = numpy.log(p['It']/p['I0'])

        ## use energy as the pandas index
        p.set_index('dcm_energy')
        
        ## comment and separator lines
        handle.write('# //////////////////////////////////////////////////////////\n')
        for l in xdi["_comment"]:
            handle.write(f'# {l}\n')
        handle.write('# ----------------------------------------------------------\n')
        handle.write('# ')

        ## dump the data table and close the file
        handle.write(p.to_csv(None, sep=' ', columns=column_list, index=False, header=column_labels, float_format='%.6f'))
        handle.flush()
        handle.close()

        log_entry(logger, f'wrote XAS data to {fname}')


    def everyxas(self, catalog=None, gup=None, since=None, until=None, groupby='webcam', logger=None):
        if gup is None or since is None or until is None:
            print('insufficient information for generating XAS files')
            return

        
        timequery = TimeRange(since=since, until=until, timezone="US/Eastern")
        timespan  = catalog.search(timequery)                         # all scans within the time window -->
        allscans  = timespan.search(Eq('proposal.proposal_id', gup))  # all scans within time window AND from GUP experimemt -->
        allxas    = allscans.search(Regex('plan_name', 'scan_nd'))    # only the XAS scans

        ## figure out how to recognize the scans in a sequence
        if groupby is not None:
            if groupby not in ('webcam', 'anacam', 'usbcam', 'usbcam1', 'usbcam2', 'cam8', 'cam9', 'xrf', 'ga'):
                groupby = 'webcam'
            elif groupby in ('cam8', 'cam9'):
                groupby = 'gige'
            if groupby == 'ga':
                groupby = 'ga_yuid'
            elif groupby == 'usbcam':
                groupby = 'usbcam2_uid'
            elif groupby == 'gige':
                groupby = 'cam8_uid'
            else:
                groupby = groupby + '_uid'
            
        ## weed out the incomplete scans
        result = []
        groups = {}
        for x in allxas:
            #print(x, allxas[x].stop['num_events']['primary'], allxas[x].start['num_points'])
            if allxas[x].stop['num_events']['primary'] == allxas[x].start['num_points']:
                ## if complete, generate the data file
                self.to_xdi(catalog=catalog, uid=x, logger=logger)
                ## gather together scans from sequences for the purpose of generating dossier files
                grouping_uid = allxas[x].start['XDI']['_snapshots'][groupby]
                if grouping_uid in groups:
                    groups[grouping_uid].append(x)
                else:
                    groups[grouping_uid] = [x,]

        ## generate dossier files
        if groupby is not None:
            for k in groups.keys():
                dossier = BMMDossier()
                dossier.rid = str(uuid.uuid4())[:8]
                dossier.uidlist = groups[k]
                dossier.write(catalog, logger)
        

class SEADFile():

    def to_xdi(self, catalog=None, uid=None, filename=None, logger=None):
        '''Write a single energy absorption detection (SEAD) scan file in XDI
        format, which is a timescan at a specific energy.

        '''
        xdi = catalog[uid].metadata["start"]["XDI"]
        fname = os.path.join(experiment_folder(catalog, uid), filename)
        handle = open(fname, 'w')
        handle.write(f'# XDI/1.0 BlueSky/{bluesky_version} BMM/{pathlib.Path(sys.executable).parts[-3]}\n')

        ## header lines with metadata from the XDi dictionary
        for family in ('Beamline', 'Detector', 'Element', 'Facility', 'Mono', 'Sample', 'Scan'):
            for k in xdi[family].keys():
                if family == 'Sample' and k == 'comment':
                    continue
                if family == 'Sample' and k == 'extra_metadata':
                    continue
                if family == 'Scan' and k == 'edge_energy':
                    handle.write(f'# {family}.mono_energy: {xdi[family][k]}\n')
                else:
                    handle.write(f'# {family}.{k}: {xdi[family][k]}\n')
        start = datetime.datetime.fromtimestamp(catalog[uid].start['time']).strftime("%Y-%m-%dT%H:%M:%S") # '%A, %d %B, %Y %I:%M %p')
        end   = datetime.datetime.fromtimestamp(catalog[uid].stop['time']).strftime("%Y-%m-%dT%H:%M:%S") # '%A, %d %B, %Y %I:%M %p')
        handle.write(f'# Scan.start_time: {start}\n')
        handle.write(f'# Scan.end_time: {end}\n')
        handle.write(f'# Scan.uid: {uid}\n')
        handle.write(f'# Scan.transient_id: {catalog[uid].metadata["start"]["scan_id"]}\n')
        handle.write( '# Column.1: energy eV\n')
        handle.write( '# Column.2: requested_energy eV\n')
        handle.write( '# Column.3: measurement_time seconds\n')
        handle.write( '# Column.4: xmu\n')
        handle.write( '# Column.5: I0 nA\n')
        handle.write( '# Column.6: It nA\n')
        handle.write( '# Column.7: Ir nA\n')

        ## Column.N header lines
        column_list = ['I0', 'It', 'Ir']
        column_labels = ['time', 'I0', 'It', 'Ir']
        
        el = catalog[uid].start['XDI']['Element']['symbol']
        nchan = 0
        if '1-element SDD' in catalog[uid].start['detectors']:
            nchan = 1
            column_list.append(f'{el}8')
            column_labels.append(f'{el}8')
            handle.write(f'# Column.8: {el}8\n')
        elif '4-element SDD' in catalog[uid].start['detectors']:
            column_list.extend([f'{el}1', f'{el}2', f'{el}3', f'{el}4'])
            column_labels.extend([f'{el}1', f'{el}2', f'{el}3', f'{el}4'])
            nchan = 4
        elif '7-element SDD' in catalog[uid].start['detectors']:
            column_list.extend([f'{el}1', f'{el}2', f'{el}3', f'{el}4', f'{el}5', f'{el}6', f'{el}7'])
            column_labels.extend([f'{el}1', f'{el}2', f'{el}3', f'{el}4', f'{el}5', f'{el}6', f'{el}7'])
            nchan = 7
        if nchan > 0:
            for i in range(1, nchan+1):
                handle.write(f'# Column.{i+7}: {el}{i}\n')

        ## prepare data table and insert reltime column
        xa = catalog[uid].primary.read(column_list)
        p = xa.to_pandas()
        t = numpy.array(catalog[uid].primary.data['time'])
        p['time'] = t - t[0]
        column_list.insert(0, 'time')

        ## use time as the pandas index
        p.set_index('time')
        
        ## comment and separator lines
        handle.write('# //////////////////////////////////////////////////////////\n')
        for l in xdi["_comment"]:
            handle.write(f'# {l}\n')
        handle.write('# ----------------------------------------------------------\n')
        handle.write('# ')

        ## dump the data table and close the file
        handle.write(p.to_csv(None, sep=' ', columns=column_list, index=False, header=column_labels, float_format='%.6f'))
        handle.flush()
        handle.close()

        log_entry(logger, f'wrote SEAD data to {fname}')



class LSFile():

    def determine_element(self, catalog, uid):
        labels = list(catalog[uid].primary.read().keys())
        for l in labels:
            m = re.match('^[A-Z][a-z]?[1-7]', l)
            if m is not None:
                return(m.string[:-1])
        return 'MCA'
        
    def to_xdi(self, catalog=None, uid=None, filename=None, logger=None):
        startdoc = catalog[uid].metadata["start"]
        fname = os.path.join(experiment_folder(catalog, uid), filename)
        handle = open(fname, 'w')
        handle.write(f'# XDI/1.0 BlueSky/{bluesky_version} BMM/{pathlib.Path(sys.executable).parts[-3]}\n')

        start = datetime.datetime.fromtimestamp(catalog[uid].start['time']).strftime("%Y-%m-%dT%H:%M:%S") # '%A, %d %B, %Y %I:%M %p')
        handle.write(f'# Scan.start_time: {start}\n')
        handle.write(f'# Scan.uid: {uid}\n')
        handle.write(f'# Scan.transient_id: {catalog[uid].metadata["start"]["scan_id"]}\n')

        motor = startdoc["motors"][0]
        column_list = [motor, 'I0', 'It', 'Ir']
        column_labels = [motor, 'I0', 'It', 'Ir']
        
        el = self.determine_element(catalog, uid)
        handle.write(f'# Column.1: {motor}\n')
        handle.write( '# Column.2: I0 nA\n')
        handle.write( '# Column.3: It nA\n')
        handle.write( '# Column.4: Ir nA\n')
        if '1-element SDD' in catalog[uid].start['detectors']:
            nchan = 1
            column_list.append(f'{el}8')
            column_labels.append(f'{el}8')
            handle.write(f'# Column.5: {el}8\n')
        elif '4-element SDD' in catalog[uid].start['detectors']:
            column_list.extend([f'{el}1', f'{el}2', f'{el}3', f'{el}4'])
            column_labels.extend([f'{el}1', f'{el}2', f'{el}3', f'{el}4'])
            nchan = 4
        elif '7-element SDD' in catalog[uid].start['detectors']:
            column_list.extend([f'{el}1', f'{el}2', f'{el}3', f'{el}4', f'{el}5', f'{el}6', f'{el}7'])
            column_labels.extend([f'{el}1', f'{el}2', f'{el}3', f'{el}4', f'{el}5', f'{el}6', f'{el}7'])
            nchan = 7
        if nchan > 0:
            for i in range(1, nchan+1):
                handle.write(f'# Column.{i+4}: {el}{i}\n')

        ## prepare data table and insert reltime column
        xa = catalog[uid].primary.read(column_list)
        p = xa.to_pandas()
        t = numpy.array(catalog[uid].primary.data['time'])

        ## use time as the pandas index
        p.set_index(motor)
        
        ## comment and separator lines
        handle.write( '# //////////////////////////////////////////////////////////\n')
        handle.write(f'# linescan on {motor}\n')
        handle.write( '# ----------------------------------------------------------\n')
        handle.write( '# ')

        ## dump the data table and close the file
        handle.write(p.to_csv(None, sep=' ', columns=column_list, index=False, header=column_labels, float_format='%.6f'))
        handle.flush()
        handle.close()

        log_entry(logger, f'wrote linescan data to {fname}')



class RasterFiles():

    def preserve_data(self, catalog, uid, logger):
        '''Save the data from an areascan as a .xlsx file (a simple spreadsheet
        which can be ingested by many plotting programs) and as a .mat
        file (which can be ingested by Matlab).

        to do:
        1. save all Xspress3 columns
        2. 1- and 7-element detectors
        '''

        record  = catalog[uid]
        xlsxout = os.path.join(experiment_folder(catalog, uid), record.start['XDI']['_snapshots']['xlsxout'])
        matout  = os.path.join(experiment_folder(catalog, uid), record.start['XDI']['_snapshots']['matout'])

        
        motors = record.start['motors']
        #print('Reading data set...')
        datatable = record.primary.read()

        slow = numpy.array(datatable[motors[0]])
        fast = numpy.array(datatable[motors[1]])
        i0   = numpy.array(datatable['I0'])
        it   = numpy.array(datatable['It'])
        ir   = numpy.array(datatable['Ir'])

        if '4-element SDD' in catalog[uid].start['detectors'] or 'if' in catalog[uid].start['detectors'] or 'xs' in catalog[uid].start['detectors']:
            det_name = catalog[uid].start['plan_name'].split()[-1]
            det_name = det_name[:-1]
            z = numpy.array(datatable[det_name+'1'])+numpy.array(datatable[det_name+'2'])+numpy.array(datatable[det_name+'3'])+numpy.array(datatable[det_name+'4'])
        elif 'noisy_det' in catalog[uid].start['detectors']:
            det_name = 'noisy_det'
            z = numpy.array(datatable['noisy_det'])
        else:
            det_name = catalog[uid].start['plan_name'].split()[-1]
            z = numpy.zeros(len(slow))
            
        ## save map in xlsx format
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = record.start['XDI']['Sample']['name']
        ws1.append((motors[0], motors[1], f'{det_name}/I0', det_name, 'I0', 'It', 'Ir'))
        for i in range(len(slow)):
            ws1.append((slow[i], fast[i], z[i]/i0[i], z[i], i0[i], it[i], ir[i]))
        wb.save(xlsxout)
        log_entry(logger, f'wrote {xlsxout}')

        ## save map in matlab format 
        savemat(matout, {'label'   : record.start['XDI']['Sample']['name'],
                         motors[0] : list(slow),
                         motors[1] : list(fast),
                         'I0'      : list(i0),
                         'It'      : list(it),
                         'Ir'      : list(ir),
                         'signal'  : list(z),})
        log_entry(logger, f'wrote {matout}')

