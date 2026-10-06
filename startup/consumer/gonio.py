
import numpy, os, sys, pandas, pathlib, datetime, re, time
from bluesky import __version__ as bluesky_version

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import ColorFormat, RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE
import datetime

from tools import echo_slack, experiment_folder, profile_configuration

from bmm_tools.devices.attenuators import KNOWN_ATTENUATION
from bmm_tools.tools.db import file_resource

from tools import experiment_folder, file_resource, profile_configuration

def log_entry(logger, message):
    #if logger.name == 'BMM file manager logger' or logger.name == 'bluesky_kafka':
    #print(message)
    #post_to_slack(message)
    #echo_slack(text = message,
    #           icon = 'message',
    #           rid  = None )
    logger.info(message)

startup_dir = profile_configuration['services']['startup']


class XRRFile():


    
    def to_xdi(self, catalog=None, uid=None, stub=None, logger=None):
        '''Write an XDI-style file for an XRR scan.

        '''


        etaval, count = None, 0
        while etaval is None:
            try:
                etaval = float(catalog[uid].baseline['eta'].read()[0])
            except:
                pass
            if etaval is not None:
                break
            count += 1
            if count > 6:
                return
            this_pause = 0.1 * 2**count
            print(f"looking for eta array in primary {count = }, {this_pause = }", flush=True)
            time.sleep(this_pause)
            


        
        metadata = catalog[uid].metadata
        xdi = metadata["start"]["XDI"]
        if stub is None:
            stub = xdi['_filename']
        fname = os.path.join(experiment_folder(catalog, uid), stub+'.xdi')
        fname = self.unclobbered_filename(fname)
        handle = open(fname, 'w')
        handle.write(f'# XDI/1.0 BlueSky/{bluesky_version} BMM/{pathlib.Path(sys.executable).parts[-3]}\n')
        
        ## header lines with metadata from the XDi dictionary
        for family in ('Beamline', 'Detector', 'Element', 'Facility', 'Mono', 'Sample', 'Scan'):
            for k in xdi[family].keys():
                if family == 'Sample' and k == 'comment':
                    continue
                if family == 'Sample' and k == 'extra_metadata':
                    continue
                if family == 'Beamline' and k in ('eta_refinement', 'sample_alignment'):
                    continue
                if family == 'Detector' and k.startswith('mythen_'):
                    continue
                handle.write(f'# {family}.{k}: {xdi[family][k]}\n')
        start = datetime.datetime.fromtimestamp(metadata['start']['time']).strftime("%Y-%m-%dT%H:%M:%S") # '%A, %d %B, %Y %I:%M %p')
        #end   = datetime.datetime.fromtimestamp(metadata['stop']['time']).strftime("%Y-%m-%dT%H:%M:%S") # '%A, %d %B, %Y %I:%M %p')

        ## check age of record with
        ## (datetime.datetime.now() - datetime.datetime.fromtimestamp(metadata['start']['time'])).seconds
        ## if it is old enough , use the stop doc, else use now() and "(approximate)"
        
        end   = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S") # '%A, %d %B, %Y %I:%M %p')
        handle.write(f'# Scan.start_time: {start}\n')
        handle.write(f'# Scan.end_time: {end} (approximate)\n')
        handle.write(f'# Scan.uid: {uid}\n')
        handle.write(f'# Scan.transient_id: {metadata["start"]["scan_id"]}\n')

        if 'mythen-2' in metadata['start']['detectors']:
            hdf5files = file_resource(catalog, uid)
            for h in hdf5files:
                relative = '/'.join(h.split('/')[-6:])
                if 'mythen' in relative:
                    handle.write(f'# Scan.mythen_hdf5_file: {relative}\n')

        #handle.write( '# Scan.plot_hint: \n')
        handle.write( '# Column.1: eta degrees\n')
        handle.write( '# Column.2: delta degrees\n')
        handle.write( '# Column.3: measurement_time seconds\n')
        handle.write( '# Column.4: monitor counts\n')
        handle.write( '# Column.5: mca_full counts\n')
        handle.write( '# Column.6: dir counts\n')
        handle.write( '# Column.7: attenuator\n')
        handle.write( '# Column.8: XRR\n')


        
        ## Column.N header lines
        column_list = ['eta', 'delta', 'dwti_dwell_time', 'monitor', 'mca_full', 'dir', 'refl', 'max_counts', 'attenuator_attenuation']
        column_labels = ['eta', 'delta', 'measurement_time', 'monitor', 'mca_full', 'dir', 'refl', 'max_counts', 'attenuator', 'xrr']

        xa = catalog[uid].primary.read(column_list)
        p = xa.to_pandas()
        i = numpy.array(p['attenuator_attenuation']).astype(int)  # fetch attenuation factor from attenuator settings
        
        factor = numpy.array(list(KNOWN_ATTENUATION[x] for x in i))
        p['xrr'] = factor * p['refl'] / p['monitor'] / p['dwti_dwell_time']  # insert reduced XRR into dataframe
        column_list.append('xrr')
        
        ## use eta as the pandas index
        p.set_index('eta')

        ## comment and separator lines
        handle.write('# //////////////////////////////////////////////////////////\n')
        if '_comment' in xdi:
            for l in xdi["_comment"]:
                handle.write(f'# {l}\n')
            else:
                handle.write(f'# \n')
            handle.write('# ----------------------------------------------------------\n')
        handle.write('# ')

        ## dump the data table and close the file
        handle.write(p.to_csv(None, sep=' ', columns=column_list, index=False, header=column_labels, float_format='%.6f'))
        handle.flush()
        handle.close()

        log_entry(logger, f'wrote XRR data to {fname}')


    def to_txt(self, catalog=None, uid=None, stub=None, logger=None, style='short'):

        header = '''% Description of the file: <name of detector> <columns>
%                      or: <name of detector> <first column> <last column>
%
%D	Delta	1
%D	Eta	2
%D	Nu	3
%D	Wheel 1	4
%D	mca	5
%D	dir	6
%D	Monitor	7
%D	Seconds	8
'''
        longheader = '%D	LinearDetector	9	1288\n'

        nuval, count = None, 0
        while nuval is None:
            try:
                nuval = float(catalog[uid].baseline['nu'].read()[0])
            except:
                pass
            if nuval is not None:
                break
            count += 1
            if count > 6:
                return
            this_pause = 0.1 * 2**count
            print(f"looking for nu in baseline {count = }, {this_pause = }", flush=True)
            time.sleep(this_pause)
            

        column_list = ['delta', 'eta', 'attenuator_attenuation', 'mca_full', 'dir', 'monitor', 'dwti_dwell_time']
        xa = catalog[uid].primary.read(column_list)
        p = xa.to_pandas()
        column_list.insert(2, 'nu')
        npoints = len(catalog[uid].primary['eta'].read())
        nu = nuval * numpy.ones(npoints)
        p['nu'] = nu


        if style in ('short', 'both'):
            fname = os.path.join(experiment_folder(catalog, uid), stub+'_short.txt')
            fname = self.unclobbered_filename(fname)
            handle = open(fname, 'w')
            handle.write(header)
            handle.write('\n')
            handle.write(p.to_csv(None, sep='\t', columns=column_list, index=False, header=False, float_format='%.6f'))
            handle.flush()
            handle.close()
            
            log_entry(logger, f'wrote XRR data to {fname}')

        
        if style in ('long', 'both'):
            fullmca = catalog[uid].primary.read()['mythen-2_image'][:,0,:].astype(int)
            mcabins = list((f'bin{i+1}' for i in range(fullmca.shape[-1]) ))
            mcaFrame = pandas.DataFrame(fullmca, columns=mcabins)
            
            fname = os.path.join(experiment_folder(catalog, uid), stub+'_XRR_MAT')
            fname = self.unclobbered_filename(fname)
            handle = open(fname, 'w')
            handle.write(header)
            handle.write(longheader)
            handle.write('\n')

            p = p.join(mcaFrame)
            handle.write(p.to_csv(None, sep='\t', columns=column_list+mcabins, index=False, header=False, float_format='%.6f'))
            handle.flush()
            handle.close()

            log_entry(logger, f'wrote XRR data to {fname}')


    def unclobbered_filename(self, filename):
        if os.path.exists(filename) is False:
            return filename
        name, extension = os.path.splitext(filename)
        pattern = re.compile('\\((\\d+)\\)$')
        s = pattern.search(name)
        if s is None:
            name = name + '(1)'
        else:
            was = s.group()
            next_index = int(s.groups()[0]) + 1
            name = name.replace(was, f'({next_index})')
        return name+extension

        
    def linescan_file(self, catalog=None, uid=None, stub=None, motor=None, detector=None, logger=None):
        header = f'''% Description of the file: <name of detector> <columns>
%                      or: <name of detector> <first column> <last column>
%
%D	{motor.capitalize()}		1
'''
        n = 2
        if detector in ('mythen', 'dir', 'refl', 'mythen_dir', 'mythen_refl'):
            header += '%D	mca_full	2\n'
            header += '%D	dir		3\n'
            header += '%D	refl		4\n'
            header += '%D	max_counts	5\n'
            column_list = [motor, 'mca_full', 'dir', 'refl', 'max_counts', 'monitor', 'dwti_dwell_time']
            n = 5
        elif detector.lower() == 'mca_full':
            header += '%D	mca_full	2\n'
            column_list = [motor, 'mca_full', 'dir', 'dwti_dwell_time']
            n = 2
        elif detector.lower() == 'mca_narrow':
            header += '%D	mca_narrow	2\n'
            column_list = [motor, 'mca_full', 'dir', 'dwti_dwell_time']
            n = 2

        header += f'%D	Monitor	{n+1}\n'
        header += f'%D	Seconds	{n+2}\n'
        header += f'%D	LinearDetector	{n+3}	{n+1283}\n'

        xa = catalog[uid].primary.read(column_list)
        p = xa.to_pandas()
        
        fullmca = catalog[uid].primary.read()['mythen-2_image'][:,0,:].astype(int)
        mcabins = list((f'bin{i+1}' for i in range(fullmca.shape[-1]) ))
        mcaFrame = pandas.DataFrame(fullmca, columns=mcabins)

        fname = os.path.join(experiment_folder(catalog, uid), stub+'.dat')
        fname = self.unclobbered_filename(fname)
        handle = open(fname, 'w')
        handle.write(header)

        p = p.join(mcaFrame)
        handle.write(p.to_csv(None, sep='\t', columns=column_list+mcabins, index=False, header=False, float_format='%.6f'))
        handle.flush()
        handle.close()

        log_entry(logger, f'wrote XRR linescan to {fname}')
            
    def calibration_file(self, catalog=None, results=None, stub=None, motor=None, detector=None, logger=None):
        header = f'''% Description of the file: <name of detector> <columns>
%                      or: <name of detector> <first column> <last column>
%
%D	Delta	1
%D	Eta	2
%D	Monitor	3
%D	LinearDetector  4	1283

'''
        etaval = catalog[uid].baseline['eta'].read()[0]
        column_list = ['delta', 'monitor']
        xa = catalog[uid].primary.read(column_list)
        p = xa.to_pandas()
        column_list.insert(2, 'eta')
        npoints = len(catalog[uid].primary.read()['delta'])
        eta = etaval * numpy.ones(npoints)
        p['eta'] = eta

        fullmca = catalog[uid].primary.read()['mythen-2_image'][:,0,:].astype(int)
        mcabins = list((f'bin{i+1}' for i in range(fullmca.shape[-1]) ))
        mcaFrame = pandas.DataFrame(fullmca, columns=mcabins)

        

        fname = os.path.join(experiment_folder(catalog, uid), stub+'.dat')
        fname = self.unclobbered_filename(fname)
        handle = open(fname, 'w')
        handle.write(header)

        p = p.join(mcaFrame)
        handle.write(p.to_csv(None, sep='\t', columns=column_list+mcabins, index=False, header=False, float_format='%.6f'))
        handle.flush()
        handle.close()

        log_entry(logger, f'wrote XRR data to {fname}')

    def eta_refinement_report(self, catalog=None, results=None, path=None, stub=None, folder=None, logger=None):
        ## make a pptx with a single blank slide and no placeholders
        prs = Presentation()
        slide_layout = prs.slide_layouts[5]  #  title only
        slide = prs.slides.add_slide(slide_layout)
        
        ## make a Text box at the top with the path to the proposal
        ## folder and the UID of the calibration scan
        top = Inches(0.2)
        left = Inches(1.5)
        width = Inches(1)
        height = Inches(1)
        txBox1 = slide.shapes.add_textbox(left, top, width, height)
        tf1 = txBox1.text_frame
        p = tf1.paragraphs[0]
        run = p.add_run()
        run.text = f'{path}'
        run.font.size = Pt(10)

        title = slide.shapes.title
        title.text = stub

        ## sequence of boxes for the figures
        top=Inches(1.3)
        width=Inches(1.8)
        height=Inches(1.6)

        for i,r in enumerate(results):
            left=Inches(2*i+0.8)
            pic = slide.shapes.add_picture(os.path.join(path, folder, f'{stub}_eta_{results[i]["eta_nominal"]}.png'),
                                           left, top, width=width, height=height)
            #                                 left          top          width        height
            txBox = slide.shapes.add_textbox(Inches(2*i+0.8), Inches(3.0), Inches(1.8), Inches(0.1))
            tf = txBox.text_frame
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = results[i]['uid']
            run.font.size = Pt(8)


        shapes = slide.shapes
        table = shapes.add_table(len(results)+1, 5, Inches(1.0), Inches(4.0), Inches(1), Inches(1)).table
        # set column widths
        table.columns[0].width = Inches(1.5)
        table.columns[1].width = Inches(1.5)
        table.columns[2].width = Inches(1.5)
        table.columns[3].width = Inches(1.5)
        table.columns[4].width = Inches(1.5)

        
        table.cell(0, 0).text = 'Eta(nominal)'
        table.cell(0, 1).text = 'delta'
        table.cell(0, 2).text = 'attenuator'
        table.cell(0, 3).text = 'eta_found'
        table.cell(0, 4).text = 'difference'

        answer = 0
        for i,r in enumerate(results):
            table.cell(i+1, 0).text = str(round(r['eta_nominal'],4))
            table.cell(i+1, 1).text = str(round(r['delta'],4))
            table.cell(i+1, 2).text = str(r['attenuator'])
            table.cell(i+1, 3).text = str(round(r['eta_found'],4))
            table.cell(i+1, 4).text = str(round(r['diff'],4))
            answer += r['diff']
        answer = answer / len(results)
            
            
        txBox = slide.shapes.add_textbox(Inches(1.8), Inches(6.5), Inches(3.8), Inches(0.1))
        tf = txBox.text_frame
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = f'mean difference = {answer:.4f}'
        run.font.size = Pt(18)
            
        
        if os.path.exists(os.path.join(path, 'reports')) is False:
            os.makedirs(os.path.join(path, 'reports'))
            logger.info(f'made directory {os.path.join(path, "reports")}')

        pptxfile = os.path.join(path, 'reports', f'{stub}_eta_refinement.pptx')
        prs.save(pptxfile)
        log_entry(logger, f'wrote Mythen calibration report to {pptxfile}')

        
        
    def sample_alignment_report(self, catalog=None, results=None, path=None, stub=None, folder=None, logger=None):
        ## make a pptx with a single blank slide and no placeholders
        prs = Presentation()
        slide_layout = prs.slide_layouts[5]  #  title only
        slide = prs.slides.add_slide(slide_layout)
        
        ## make a Text box at the top with the path to the proposal
        ## folder and the UID of the calibration scan
        top = Inches(0.2)
        left = Inches(1.5)
        width = Inches(1)
        height = Inches(1)
        txBox1 = slide.shapes.add_textbox(left, top, width, height)
        tf1 = txBox1.text_frame
        p = tf1.paragraphs[0]
        run = p.add_run()
        run.text = f'{path}'
        run.font.size = Pt(10)

        title = slide.shapes.title
        title.text = stub
        
        ## sequence of boxes for the figures
        left=Inches(0.5)
        top=Inches(1.3)
        width=Inches(2.5)
        height=Inches(1.875)
        pic = slide.shapes.add_picture(os.path.join(path, folder, stub+'_vertical_pass1.png'),
                                       left, top, width=width, height=height)
        txBox = slide.shapes.add_textbox(Inches(0.5), Inches(3.1), Inches(2.5), Inches(0.1))
        tf = txBox.text_frame
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = results[0]['v_uid']
        run.font.size = Pt(8)

        left=Inches(3.25)
        pic = slide.shapes.add_picture(os.path.join(path, folder, stub+'_vertical_pass2.png'),
                                       left, top, width=width, height=height)
        txBox = slide.shapes.add_textbox(Inches(3.25), Inches(3.1), Inches(2.5), Inches(0.1))
        tf = txBox.text_frame
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = results[1]['v_uid']
        run.font.size = Pt(8)

        left=Inches(6)
        pic = slide.shapes.add_picture(os.path.join(path, folder, stub+'_vertical_pass3.png'),
                                       left, top, width=width, height=height)
        txBox = slide.shapes.add_textbox(Inches(6), Inches(3.1), Inches(2.5), Inches(0.1))
        tf = txBox.text_frame
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = results[2]['v_uid']
        run.font.size = Pt(8)

        
        top=Inches(3.55)
        left=Inches(0.5)
        pic = slide.shapes.add_picture(os.path.join(path, folder, stub+'_eta_pass1.png'),
                                       left, top, width=width, height=height)
        txBox = slide.shapes.add_textbox(Inches(0.5), Inches(5.35), Inches(2.5), Inches(0.1))
        tf = txBox.text_frame
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = results[0]['e_uid']
        run.font.size = Pt(8)
        
        left=Inches(3.25)
        pic = slide.shapes.add_picture(os.path.join(path, folder, stub+'_eta_pass2.png'),
                                       left, top, width=width, height=height)
        txBox = slide.shapes.add_textbox(Inches(3.25), Inches(5.35), Inches(2.5), Inches(0.1))
        tf = txBox.text_frame
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = results[1]['e_uid']
        run.font.size = Pt(8)
        
        left=Inches(6)
        pic = slide.shapes.add_picture(os.path.join(path, folder, stub+'_eta_pass3.png'),
                                       left, top, width=width, height=height)
        txBox = slide.shapes.add_textbox(Inches(6), Inches(5.35), Inches(2.5), Inches(0.1))
        tf = txBox.text_frame
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = results[2]['e_uid']
        run.font.size = Pt(8)
        

        shapes = slide.shapes
        table = shapes.add_table(4, 5, Inches(1.0), Inches(5.7), Inches(1), Inches(1)).table
        # set column widths
        table.columns[0].width = Inches(1.5)
        table.columns[1].width = Inches(1.5)
        table.columns[2].width = Inches(1.5)
        table.columns[3].width = Inches(1.5)
        table.columns[4].width = Inches(1.5)

        
        table.cell(0, 0).text = 'Iteration'
        table.cell(0, 1).text = 'samplez'
        table.cell(0, 2).text = 'eta_peak'
        table.cell(0, 3).text = 'eta_com'
        table.cell(0, 4).text = 'eta_cen'

        table.cell(1, 0).text = '1'
        table.cell(2, 0).text = '2'
        table.cell(3, 0).text = '3'

        table.cell(1, 1).text = str(round(results[0]['samplez'],4))
        table.cell(2, 1).text = str(round(results[1]['samplez'],4))
        table.cell(3, 1).text = str(round(results[2]['samplez'],4))

        table.cell(1, 2).text = str(round(results[0]['eta_peakpos'],4))
        table.cell(2, 2).text = str(round(results[1]['eta_peakpos'],4))
        table.cell(3, 2).text = str(round(results[2]['eta_peakpos'],4))

        table.cell(1, 3).text = str(round(results[0]['eta_com'],4))
        table.cell(2, 3).text = str(round(results[1]['eta_com'],4))
        table.cell(3, 3).text = str(round(results[2]['eta_com'],4))

        table.cell(1, 4).text = str(round(results[0]['eta_fwhm_center'],4))
        table.cell(2, 4).text = str(round(results[1]['eta_fwhm_center'],4))
        table.cell(3, 4).text = str(round(results[2]['eta_fwhm_center'],4))


        
        
        # top = Inches(5.7)
        # left = Inches(4.7)
        # width = Inches(1)
        # height = Inches(1)
        # txBox1 = slide.shapes.add_textbox(left, top, width, height)
        # tf1 = txBox1.text_frame
        # p = tf1.paragraphs[0]
        # run = p.add_run()
        # text  = " Iteration    samplez     eta_peak    eta_com    eta_cen \n"
        # text +="============================================================\n"
        # for i,r in enumerate(results):
        #     text+=f'    {i+1}             {r["samplez"]:8.5f}   {r["eta_peakpos"]:8.5f}    {r["eta_com"]:8.5f}   {r["eta_fwhm_center"]:8.5f}\n'
        
        # run.text = text


        
        
        if os.path.exists(os.path.join(path, 'reports')) is False:
            os.makedirs(os.path.join(path, 'reports'))
            logger.info(f'made directory {os.path.join(path, "reports")}')

        pptxfile = os.path.join(path, 'reports', f'{stub}_alignment.pptx')
        prs.save(pptxfile)
        log_entry(logger, f'wrote Mythen calibration report to {pptxfile}')


        
    def mythen_calibration(self, catalog=None, uid=None, path=None, hdffile=None, now=None, stamp=None, setup=None, gap=None,
                           energy=8600, pixel0=None, angle_per_pixel=None, stub=None, dw=3,
                           rw=9, slits_b=0.3, slits_i=0.5, slits_o=0.5, slits_t=0.3, logger=None):  # fixme! fitA, fitB, fitC
        '''Write a PowerPoint summary of the Mythen calibration using the
        established layout of the report in use by the IBM folks.

        The purpose of this calibration is to determine the central
        pixel of the beam on the detector, the distance between sample
        and detector, and the angular range subtended by a pixel.

        This method gathers that information into an agreed-upon
        presentation format.

        '''

        ## fixme!
        fitA = fitB = fitC = 0

        ## make a pptx with a single blank slide and no placeholders
        prs = Presentation()
        blank_slide_layout = prs.slide_layouts[6]
        slide = prs.slides.add_slide(blank_slide_layout)


        ## make a Text box at the top with the path to the proposal
        ## folder and the UID of the calibration scan
        top = Inches(0.2)
        left = Inches(1.5)
        width = Inches(1)
        height = Inches(1)
        txBox1 = slide.shapes.add_textbox(left, top, width, height)
        tf1 = txBox1.text_frame
        p = tf1.paragraphs[0]
        run = p.add_run()
        run.text = f'{path}\n{uid}\nhdffile'
        run.font.size = Pt(10)

        ## make a Text box for the all the header information, date,
        ## measurement type, gap, energy, calibration fit result,
        ## center pixel position, detector distance calculation
        top = Inches(0.8)
        left = Inches(4)
        width = Inches(1)
        height = Inches(1)
        txBox2 = slide.shapes.add_textbox(left, top, width, height)
        tf2 = txBox2.text_frame

        #p = tf.add_paragraph()
        tf2.text = f"{now} calibration"

        p = tf2.add_paragraph()
        p.text = f'(for {setup})'

        p = tf2.add_paragraph()
        p.text = f'Gap={gap} mm, E= {energy} keV'

        p = tf2.add_paragraph()
        p.text = f'FIT: arctan((channel - {pixel0})/{angle_per_pixel})'

        p = tf2.add_paragraph()
        p.text = f'PIXEL 0 = {pixel0}; D=0.05 x {angle_per_pixel} = {angle_per_pixel*0.05} mm'

        ## justify the first two text boxes
        for para in tf1.paragraphs:
            para.alignment = PP_ALIGN.LEFT
        for para in tf2.paragraphs:
            para.alignment = PP_ALIGN.CENTER


        ## make a box for the picture of the fit
        left=Inches(4)
        top=Inches(2.5)
        width=Inches(6)
        height=Inches(4)
        pic = slide.shapes.add_picture(os.path.join(path, 'snapshots', stub+'.png'),
                                       left, top, width=width, height=height)

        
        ## make a box for the mythen ROI settings
        left=Inches(0.9)
        top=Inches(3)
        width=Inches(3)
        height=Inches(2)

        ROIBox = slide.shapes.add_textbox(left, top, width, height)
        roitext = ROIBox.text_frame
        roitext.clear()
        p = roitext.paragraphs[0]
        run = p.add_run()
        run.text = f'''ROIs:
        dir = +/-{dw}, pixels {pixel0-dw}-{pixel0+dw}, {0.05*(2*dw+1):.2f}mm
        refl = +/-{rw}, pixels {pixel0-rw}-{pixel0+rw}, {0.05*(2*rw+1):.2f}mm
        '''
        run.font.size = Pt(10)


        ## make a box for the slit settings
        left=Inches(0.6)
        top=Inches(4.5)
        width=Inches(3)
        height=Inches(2)

        SlitsBox = slide.shapes.add_textbox(left, top, width, height)
        slitstext = SlitsBox.text_frame
        slitstext.clear()
        p = slitstext.paragraphs[0]
        run = p.add_run()
        run.text = f'''Incident slits:
        s1t, s1b = {slits_t:.2f}, V={2*slits_t:.2f}
        s1o, s1i = {slits_o:.2f}, H={2*slits_o:.2f}
        '''
        run.font.size = Pt(14)



        if 'pole' in setup.lower():
            left=Inches(3)
            top=Inches(6.5)
            width=Inches(6)
            height=Inches(1)

            CalBox = slide.shapes.add_textbox(left, top, width, height)
            caltext = CalBox.text_frame
            caltext.clear()
            p = caltext.paragraphs[0]
            run = p.add_run()
            run.text = 'CHESS calibration: 2θ = ({fitA}*pixel² + {fitB}*pixel + {fitC}) + del'
            run.font.size = Pt(14)


        run.font.size = Pt(10)

        if os.path.exists(os.path.join(path, 'reports')) is False:
            os.makedirs(os.path.join(path, 'reports'))
            logger.info(f'made directory {os.path.join(path, "reports")}')

        fname = os.path.join(path, 'reports', f'{stub}_{stamp}.pptx')
        prs.save(fname)
        log_entry(logger, f'wrote Mythen calibration report to {fname}')


    def baseline_table(self, baseline, tab=''):

        text = ''

        def one_table(header=None, axis_list=(), columns=5):
            this = ''
            if header is not None:
                this += f'{tab}<table class="baseline">\n'
                this += f'{tab}  <tr>\n'
                this += f'{tab}    <th colspan={columns} style="font-size:12pt">{header}</th>\n'
                this += f'{tab}  </tr>\n'

            this += f'{tab}  <tr>\n'
            for thing in axis_list:
                try:
                    this += f'{tab}    <td>{thing}, {float(baseline[thing][0]):.4f}</td>\n'
                except:
                    this += f'{tab}    <td>{thing}, (missing)</td>\n'
            this += f'{tab}  </tr>\n'
            return this


        text += one_table(header='Goniometer',
                          columns=8,
                          axis_list=('delta', 'eta', 'chi', 'phi', 'mu', 'nu', 'analyzer', 'detector', ))
        text += one_table(columns=8,
                          axis_list=('dethor', 'wheel1', 'shield', 'samplex', 'sampley', 'samplez', 'table_yd', 'table_yui', ))
        text += one_table(columns=8,
                          axis_list=('table_yuo', 'table_xu', 'table_xd', 'table_z', 'slits_t', 'slits_b', 'slits_i', 'slits_o'))
        text += f'{tab}</table><p></p>\n'



        text += one_table(header='Monochromator',
                          columns=7,
                          axis_list=('dcm_bragg', 'dcm_para', 'dcm_perp', 'dcm_pitch', 'dcm_roll', 'dcm_x', 'dcm_y', ))
        text += f'{tab}</table><p></p>\n'

        text += one_table(header='Focusing mirror',
                          axis_list=('m2_yu', 'm2_ydo', 'm2_ydi', 'm2_xu', 'm2_xd') )
        text += one_table(axis_list=('m2_vertical', 'm2_lateral', 'm2_pitch', 'm2_roll', 'm2_yaw'))
        text += one_table(axis_list=('m2_bender', ))
        text += f'{tab}</table><p></p>\n'

        text += one_table(header='Harmonic rejection mirror',
                          axis_list=('m3_yu', 'm3_ydo', 'm3_ydi', 'm3_xu', 'm3_xd'))
        text += one_table(axis_list=('m3_vertical', 'm3_lateral', 'm3_pitch', 'm3_roll', 'm3_yaw'))
        text += f'{tab}</table><p></p>\n'

        text += one_table(header='Slits2 (post-mono)',
                          columns=4,
                          axis_list=('slits2_top', 'slits2_bottom', 'slits2_outboard', 'slits2_inboard'))
        text += one_table(columns=4,
                          axis_list=('slits2_vsize', 'slits2_vcenter', 'slits2_hsize', 'slits2_hcenter'))
        text += f'{tab}</table><p></p>\n'

        text += one_table(header='Slits3 (hutch entrance)',
                          columns=4,
                          axis_list=('slits3_top', 'slits3_bottom', 'slits3_outboard', 'slits3_inboard'))
        text += one_table(columns=4,
                          axis_list=('slits3_vsize', 'slits3_vcenter', 'slits3_hsize', 'slits3_hcenter'))
        text += f'{tab}</table><p></p>\n'

        text += one_table(header='Hutch slit assembly and XAFS table',
                          columns=5,
                          axis_list=('dm3_bct', 'xafs_table_yu', 'xafs_table_yd', 'xafs_table_vertical', 'xafs_table_pitch'))
        text += f'{tab}</table><p></p>\n'


        return text

        
    def dossier(self, catalog=None, uid=None, stub=None, logger=None):

        metadata = catalog[uid].metadata
        baseline = catalog[uid].baseline.read()

        thiscontent = ''
        pfolder = experiment_folder(catalog, uid)
        
        ## top of HTML file
        with open(os.path.join(startup_dir, 'consumer', 'xrr_tmpl', 'dossier_top.tmpl')) as f:
            content = f.readlines()
        thiscontent += ''.join(content).format(stub = stub,
        )           
        ## XRR section
        with open(os.path.join(startup_dir, 'consumer', 'xrr_tmpl', 'dossier_xrr.tmpl')) as f:
            content = f.readlines()
        thiscontent += ''.join(content).format(stub = stub,
                                               composition   = metadata['start']['XDI']['Sample']['name'],
                                               preparation   = metadata['start']['XDI']['Sample']['prep'],
                                               comment       = '...',
                                               start         = datetime.datetime.fromtimestamp(metadata['start']['time']).strftime("%Y-%m-%dT%H-%M-%S"),
                                               stop          = datetime.datetime.fromtimestamp(metadata['stop']['time']).strftime("%Y-%m-%dT%H-%M-%S"),
                                               gup           = metadata['start']['XDI']['Facility']['GUP'],
                                               saf           = metadata['start']['XDI']['Facility']['SAF'],
                                               cycle         = metadata['start']['XDI']['Facility']['cycle'],
                                               beamline      = metadata['start']['XDI']['Beamline']['name'],
                                               source        = metadata['start']['XDI']['Beamline']['xray_source'],
                                               collimation   = metadata['start']['XDI']['Beamline']['collimation'],
                                               focusing      = metadata['start']['XDI']['Beamline']['focusing'],
                                               software      = metadata['start']['XDI']['Beamline']['software'],
                                               endstation    = metadata['start']['XDI']['Beamline']['endstation'],
                                               hklpy2_mode   = metadata['start']['XDI']['Beamline']['hklpy2_mode'],
                                               folder        = pfolder,
                                               experimenters = metadata['start']['XDI']['Scan']['experimenters'],
                                               uid           = uid,
                                               hdffile       = file_resource(catalog, uid)[0].replace(pfolder+'/', ''),
                                               monitor       = metadata['start']['XDI']['Detector']['I0'],
                                               detector      = metadata['start']['XDI']['Detector']['XRR'],
        )            

        ## cameras section
        with open(os.path.join(startup_dir, 'consumer', 'xrr_tmpl', 'dossier_cameras.tmpl')) as f:
            content = f.readlines()
        thiscontent += ''.join(content).format(stub = stub,)

        
        ## sample alignment section
        with open(os.path.join(startup_dir, 'consumer', 'xrr_tmpl', 'dossier_align.tmpl')) as f:
            content = f.readlines()
        results = metadata['start']['XDI']['Beamline']['sample_alignment']
        thiscontent += ''.join(content).format(stub  = stub,
                                               
                                               euid1 = results[0]['e_uid'],
                                               vuid1 = results[0]['v_uid'],
                                               sz1   = round(results[0]['samplez'],4),
                                               peak1 = round(results[0]['eta_peakpos'],4),
                                               com1  = round(results[0]['eta_com'],4),
                                               cen1  = round(results[0]['eta_fwhm_center'],4),
                                               
                                               euid2 = results[1]['e_uid'],
                                               vuid2 = results[1]['v_uid'],
                                               sz2   = round(results[1]['samplez'],4),
                                               peak2 = round(results[1]['eta_peakpos'],4),
                                               com2  = round(results[1]['eta_com'],4),
                                               cen2  = round(results[1]['eta_fwhm_center'],4),

                                               euid3 = results[2]['e_uid'],
                                               vuid3 = results[2]['v_uid'],
                                               sz3   = round(results[2]['samplez'],4),
                                               peak3 = round(results[2]['eta_peakpos'],4),
                                               com3  = round(results[2]['eta_com'],4),
                                               cen3  = round(results[2]['eta_fwhm_center'],4),
        )


        ## spine refinement section
        with open(os.path.join(startup_dir, 'consumer', 'xrr_tmpl', 'dossier_eta.tmpl')) as f:
            content = f.readlines()
        results = metadata['start']['XDI']['Beamline']['eta_refinement']
        answer = 0
        for r in results:
            answer += r['diff']
        answer = answer / len(results)
        thiscontent += ''.join(content).format(stub  = stub,
                                               
                                               uid1        = results[0]['uid'],
                                               nominal1    = results[0]['eta_nominal'],
                                               delta1      = round(results[0]['delta'],4),
                                               attenuator1 = results[0]['attenuator'],
                                               found1      = round(results[0]['eta_found'],4),
                                               diff1       = round(results[0]['diff'],4),
                                               
                                               uid2        = results[1]['uid'],
                                               nominal2    = results[1]['eta_nominal'],
                                               delta2      = round(results[1]['delta'],4),
                                               attenuator2 = results[1]['attenuator'],
                                               found2      = round(results[1]['eta_found'],4),
                                               diff2       = round(results[1]['diff'],4),
                                               
                                               uid3        = results[2]['uid'],
                                               nominal3    = results[2]['eta_nominal'],
                                               delta3      = round(results[2]['delta'],4),
                                               attenuator3 = results[2]['attenuator'],
                                               found3      = round(results[2]['eta_found'],4),
                                               diff3       = round(results[2]['diff'],4),
                                               
                                               uid4        = results[3]['uid'],
                                               nominal4    = results[3]['eta_nominal'],
                                               delta4      = round(results[3]['delta'],4),
                                               attenuator4 = results[3]['attenuator'],
                                               found4      = round(results[3]['eta_found'],4),
                                               diff4       = round(results[3]['diff'],4),

                                               answer      = round(answer,4),
                                               
        )

        ## Mythen/Eiger calibration section
        with open(os.path.join(startup_dir, 'consumer', 'xrr_tmpl', 'dossier_mythen.tmpl')) as f:
            content = f.readlines()
        thiscontent += ''.join(content).format(stub     = stub,
                                               gap      = metadata['start']['XDI']['Detector']['mythen_gap'],
                                               pixel0   = metadata['start']['XDI']['Detector']['mythen_pixel_zero'],
                                               distance = metadata['start']['XDI']['Detector']['mythen_distance'],
                                               dirroi   = metadata['start']['XDI']['Detector']['mythen_dir_roi'],
                                               reflroi  = metadata['start']['XDI']['Detector']['mythen_refl_roi'],
                                               angle    = metadata['start']['XDI']['Detector']['mythen_angle_per_pixel'],
                                               ndir     = 3,
                                               nrefl    = 9,
                                               sizedir  = round((2*3+1)*0.05, 2),
                                               sizerefl = round((2*9+1)*0.05, 2),
                                               s1t      = round(float(baseline["slits_t"][0]), 2),
                                               s1b      = round(float(baseline["slits_b"][0]), 2),
                                               s1o      = round(float(baseline["slits_o"][0]), 2),
                                               s1i      = round(float(baseline["slits_i"][0]), 2),
                                               sv       = round(float(baseline["slits_t"][0])+float(baseline["slits_b"][0]), 2),
                                               sh       = round(float(baseline["slits_o"][0])+float(baseline["slits_i"][0]), 2),
                                               energy   = 8600,
                                               uid      = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
                                               hdffile  = 'assets/mythen2-2/2026/09/28/xxxxxxxx-xxxx-xxxx-xxxx_000000.h5',
        )

        ## bottom of HTML file
        with open(os.path.join(startup_dir, 'consumer', 'xrr_tmpl', 'dossier_baseline.tmpl')) as f:
            content = f.readlines()
        baselinetable = self.baseline_table(baseline)
        thiscontent += ''.join(content).format(stub = stub,
                                               text = baselinetable)

        ## bottom of HTML file
        with open(os.path.join(startup_dir, 'consumer', 'xrr_tmpl', 'dossier_bottom.tmpl')) as f:
            content = f.readlines()
        thiscontent += ''.join(content).format(stub = stub,
                                               now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))

        
        ## worry about numbering!!
        htmlfilename = os.path.join(pfolder, 'reports', f'{stub}.html')
        with open(htmlfilename, 'w') as o:
            o.write(thiscontent)
        
        log_entry(logger, f'wrote Mythen calibration report to {htmlfilename}')
