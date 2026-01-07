
import numpy, os, sys, pandas, pathlib, datetime, re
from bluesky import __version__ as bluesky_version

from tools import echo_slack, experiment_folder, file_resource, profile_configuration

def log_entry(logger, message):
    #if logger.name == 'BMM file manager logger' or logger.name == 'bluesky_kafka':
    #print(message)
    #post_to_slack(message)
    #echo_slack(text = message,
    #           icon = 'message',
    #           rid  = None )
    logger.info(message)



class XRRFile():


    # binary                0  1        2                  3                                         4
    # level                 0  1        2        3         4         5         6         7*          8
    measured_attenuation = [1, 6.85865, 47.0088, 318.6107, 2225.346, 15046.19, 97500.05, 668718.718, ]

    
    def to_xdi(self, catalog=None, uid=None, stub=None, logger=None):
        '''Write an XDI-style file for an XRR scan.

        '''
        metadata = catalog[uid].metadata
        xdi = metadata["start"]["XDI"]
        if stub is None:
            stub = xdi['_filename']
        fname = os.path.join(experiment_folder(catalog, uid), stub+'.xdi')
        handle = open(fname, 'w')
        handle.write(f'# XDI/1.0 BlueSky/{bluesky_version} BMM/{pathlib.Path(sys.executable).parts[-3]}\n')
        
        ## header lines with metadata from the XDi dictionary
        for family in ('Beamline', 'Detector', 'Element', 'Facility', 'Mono', 'Sample', 'Scan'):
            for k in xdi[family].keys():
                if family == 'Sample' and k == 'comment':
                    continue
                if family == 'Sample' and k == 'extra_metadata':
                    continue
                handle.write(f'# {family}.{k}: {xdi[family][k]}\n')
        start = datetime.datetime.fromtimestamp(metadata['start']['time']).strftime("%Y-%m-%dT%H:%M:%S") # '%A, %d %B, %Y %I:%M %p')
        end   = datetime.datetime.fromtimestamp(metadata['stop']['time']).strftime("%Y-%m-%dT%H:%M:%S") # '%A, %d %B, %Y %I:%M %p')
        handle.write(f'# Scan.start_time: {start}\n')
        handle.write(f'# Scan.end_time: {end}\n')
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
        handle.write( '# Column.6: mca_narrow counts\n')
        handle.write( '# Column.7: attenuator\n')

        ## Column.N header lines
        column_list = ['eta', 'delta', 'dwti_dwell_time', 'monitor', 'mca_full', 'mca_narrow', 'attenuator_attenuation']
        column_labels = ['eta', 'delta', 'measurement_time', 'monitor', 'mca_full', 'mac_narrow', 'attenuator']

        xa = catalog[uid].primary.read(column_list)
        p = xa.to_pandas()
        
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

        nuval = catalog[uid].baseline['data']['nu'][0]
        column_list = ['delta', 'eta', 'attenuator_attenuation', 'mca_full', 'mca_narrow', 'monitor', 'dwti_dwell_time']
        xa = catalog[uid].primary.read(column_list)
        p = xa.to_pandas()
        column_list.insert(2, 'nu')
        npoints = len(catalog[uid].primary['data']['eta'])
        nu = nuval * numpy.ones(npoints)
        p['nu'] = nu


        if style in ('short', 'both'):
            fname = os.path.join(experiment_folder(catalog, uid), stub+'_short.txt')
            handle = open(fname, 'w')
            handle.write(header)
            handle.write('\n')
            handle.write(p.to_csv(None, sep=' ', columns=column_list, index=False, header=False, float_format='%.6f'))
            handle.flush()
            handle.close()
            
            log_entry(logger, f'wrote XRR data to {fname}')

        
        if style in ('long', 'both'):
            fullmca = catalog[uid].primary['data']['mythen-2_image'][:,0,:].astype(int)
            mcabins = list((f'bin{i+1}' for i in range(fullmca.shape[-1]) ))
            mcaFrame = pandas.DataFrame(fullmca, columns=mcabins)
            
            fname = os.path.join(experiment_folder(catalog, uid), stub+'_long.txt')
            handle = open(fname, 'w')
            handle.write(header)
            handle.write(longheader)
            handle.write('\n')

            p = p.join(mcaFrame)
            handle.write(p.to_csv(None, sep=' ', columns=column_list+mcabins, index=False, header=False, float_format='%.6f'))
            handle.flush()
            handle.close()

            log_entry(logger, f'wrote XRR data to {fname}')


    def unclobbered_filename(self, filename):
        if os.path.exists(filename) is False:
            return filename
        name, extension = os.path.splitext(filename)
        pattern = re.compile('\((\d+)\)$')
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
%D	{motor.capitalize()}	1
'''
        n = 2
        if detector.lower() == 'mythen':
            header += '%D	mca_full	2\n'
            header += '%D	mca_narrow	3\n'
            column_list = [motor, 'mca_full', 'mca_narrow', 'monitor', 'dwti_dwell_time']
            n = 3
        elif detector.lower() == 'mca_full':
            header += '%D	mca_full	2\n'
            column_list = [motor, 'mca_full', 'monitor', 'dwti_dwell_time']
            n = 2
        elif detector.lower() == 'mca_narrow':
            header += '%D	mca_narrow	2\n'
            column_list = [motor, 'mca_narrow', 'monitor', 'dwti_dwell_time']
            n = 2

        header += f'%D	Monitor	{n+1}\n'
        header += f'%D	Seconds	{n+2}\n'

        header += f'%D	LinearDetector	{n+3}	{n+1283}\n'

        xa = catalog[uid].primary.read(column_list)
        p = xa.to_pandas()
        
        fullmca = catalog[uid].primary['data']['mythen-2_image'][:,0,:].astype(int)
        mcabins = list((f'bin{i+1}' for i in range(fullmca.shape[-1]) ))
        mcaFrame = pandas.DataFrame(fullmca, columns=mcabins)

        fname = os.path.join(experiment_folder(catalog, uid), stub+'.dat')
        fname = self.unclobbered_filename(fname)
        handle = open(fname, 'w')
        handle.write(header)

        p = p.join(mcaFrame)
        handle.write(p.to_csv(None, sep=' ', columns=column_list+mcabins, index=False, header=False, float_format='%.6f'))
        handle.flush()
        handle.close()

        log_entry(logger, f'wrote XRR linescan to {fname}')
            
    def calibration_file(self, catalog=None, uid=None, stub=None, motor=None, detector=None, logger=None):
        header = f'''% Description of the file: <name of detector> <columns>
%                      or: <name of detector> <first column> <last column>
%
%D	Delta	1
%D	Eta	2
%D	Monitor	3
%D	LinearDetector  4	1283

'''
        etaval = catalog[uid].baseline['data']['eta'][0]
        column_list = ['delta', 'monitor']
        xa = catalog[uid].primary.read(column_list)
        p = xa.to_pandas()
        column_list.insert(2, 'eta')
        npoints = len(catalog[uid].primary['data']['delta'])
        eta = etaval * numpy.ones(npoints)
        p['eta'] = eta

        fullmca = catalog[uid].primary['data']['mythen-2_image'][:,0,:].astype(int)
        mcabins = list((f'bin{i+1}' for i in range(fullmca.shape[-1]) ))
        mcaFrame = pandas.DataFrame(fullmca, columns=mcabins)

        

        fname = os.path.join(experiment_folder(catalog, uid), stub+'.dat')
        fname = self.unclobbered_filename(fname)
        handle = open(fname, 'w')
        handle.write(header)

        p = p.join(mcaFrame)
        handle.write(p.to_csv(None, sep=' ', columns=column_list+mcabins, index=False, header=False, float_format='%.6f'))
        handle.flush()
        handle.close()

        log_entry(logger, f'wrote XRR data to {fname}')


        
