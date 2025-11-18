import numpy, json, os, time
from tqdm import tqdm           # progress bar
from pprint import pprint

from BMMCommon.tools.periodictable import element_symbol, edge_energy, Z_number
from BMM.functions import elapsed_time

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from BMM.user_ns.base import startup_dir

try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False


from databroker import catalog
from databroker.queries import TimeRange


class BMMTelemetry():
    '''A class for figuring out the historical average overhead for an
    XAS scan at BMM

    "Overhead" is defined as the excess time beyond the measurement
    time.  The time span of the scan is computed from the time stamps
    in the start and stop document.  The measurement time is the sum
    of the dwell time column in the datatable from the measurement.

    '''
    def __init__(self):
        self.folder      = os.path.join(startup_dir, 'telemetry')
        self.json        = os.path.join(self.folder, 'telemetry.json')
        self.bc          = None
        if not is_re_worker_active():
            self.bc      = catalog['bmm']
        self._start_date = '2023-09-01'
        self.reliability = 10
        self.beamdump    = 3
        ###                         k-edges               l-edges
        self.all_elements = list(range(21, 46)) + list(range(52, 93))
        self.time_search = None
        self.xafs_search = None
        self.seen = {}

    @property
    def start_date(self):
        return self._start_date

    @start_date.setter
    def start_date(self, val):
        self._start_date = val
        self.time_search = None
        self.xafs_search = None
        
    def records(self, element=None):
        query = TimeRange(since=self.start_date)
        if self.time_search is None:
            self.time_search = self.bc.search(query)
        if self.xafs_search is None:
            self.xafs_search = self.time_search.search({'XDI._kind':'xafs'})
        element_search = self.xafs_search.search({'XDI.Element.symbol': element})
        print(f'\nNumber of records for {element} since {self.start_date}: {len(element_search)}')
        return(element_search)

    def visual_metadata(self, snapshots):
        '''Determine the amount of time to capture all four camera images,
        including the time between images.

        If the XAFS measurement was a fluorescence measurement, also
        record the time required to capture the XRF spectrun and make
        its png image.

        '''
        #print(snapshots['webcam_uid'])
        if snapshots['webcam_uid'] in self.seen:
            return 0
        net_time, between_time, xrf_time = 0,0,0
        try:
            web  = self.bc[snapshots['webcam_uid']].metadata
            ana  = self.bc[snapshots['anacam_uid']].metadata
            usb1 = self.bc[snapshots['usbcam1_uid']].metadata
            usb2 = self.bc[snapshots['usbcam2_uid']].metadata
            net_time = (web['stop']['time'] - web['start']['time']) +\
                (ana['stop']['time']  - ana['start']['time']) +\
                (usb1['stop']['time'] - usb1['start']['time']) +\
                (usb2['stop']['time'] - usb2['start']['time'])
            between_time = (ana['start']['time'] - web['stop']['time']) +\
                (usb1['start']['time'] - ana['stop']['time']) +\
                (usb2['start']['time'] - usb1['stop']['time'])
            if 'xrf_uid' in snapshots:
                xrf = self.bc[snapshots['xrf_uid']].metadata
                xrf_time = (xrf['stop']['time'] - xrf['start']['time']) + (web['start']['time'] - xrf['stop']['time'])
            self.seen[snapshots['webcam_uid']] = 1
        except:
            pass
        #print(net_time, between_time)
        return(net_time + between_time, xrf_time)
        
            
    def overhead(self, element=None):
        '''Determine the average overhead for all scans in a time period and
        of a particular element.  Also record the time taken to
        capture the visual metadata.

        '''
        if element is None: return({})
        element_search = self.records(element)
        l = len(element_search)
        if l == 0: return({})
        ratio, difference, dpp, visual, xrf = numpy.zeros(l), numpy.zeros(l), numpy.zeros(l), numpy.zeros(l), numpy.zeros(l)
        count = 0
        start = time.time()
        self.seen = {}
        for u, this in tqdm(element_search.items()):
            ## records that did not complete normally
            #if this is None: continue
            md = this.metadata
            if md is None or md['stop'] is None: continue
            if 'primary' in md['stop']['num_events']:
                if md['start']['num_points'] != md['stop']['num_events']['primary']:
                    continue
            try:
                t = this['primary', 'data', 'dwti_dwell_time'][:]
                measurement_time = float(t.sum())
                time_elapsed = md['stop']['time'] - md['start']['time']

                ## exclude records that span beam dumps or other pauses
                if time_elapsed/measurement_time > self.beamdump:
                    continue

                ## gather simple statistics
                difference[count] = time_elapsed - measurement_time  # total motor motion overhead
                ratio[count]      = time_elapsed/measurement_time
                dpp[count]        = difference[count] / len(t)  # approximate overhead as evenly distributed point-by-point
                visual[count], xrf[count] = self.visual_metadata(md['start']['XDI']['_snapshots'])
                count             = count + 1
            except: #  Exception as E:             # if a record cannot be processed for any reason, just skip it.
                #print(E)
                pass

        ## count will likely be smaller than l
        ratio = ratio[numpy.flatnonzero(ratio)]
        difference = difference[numpy.flatnonzero(difference)]
        dpp = dpp[numpy.flatnonzero(dpp)]
        visual = visual[numpy.flatnonzero(visual)]
        xrf = xrf[numpy.flatnonzero(xrf)]
        elapsed_time(start)
        ## return means and standard deviations
        return({'count'     : count,
                'ratio'     : [ratio.mean(), ratio.std()],
                'difference': [difference.mean(), difference.std()],
                'dpp'       : [dpp.mean(), dpp.std(), dpp.max(), dpp.min()],
                'visual'    : [visual.mean(), visual.std(), len(visual)],
                'xrf'       : [xrf.mean(), xrf.std(), len(xrf)],
            })
    

    ## TODO: take a list of integers/element symbols as an input
    ## parameter, extract just those, modify json file for those
    ## elements
    def periodic_table(self):
        start = time.time()
        results = {}
        for z in self.all_elements:
            el = element_symbol(z)
            results[el] = self.overhead(el)
            pprint(results[el])
            j = json.dumps(results)
            f = open(self.json,"w")
            f.write(j)
            f.close()
        end = time.time()
        print('\n\nThat took %.1f min' % ((end-start)/60))


    def value(self, el, thing='dpp'):
        if thing not in ('dpp', 'visual', 'xrf', 'ratio', 'difference'):
            return 0
       
        alltele = json.loads(tdata)
        return alltele[el.capitalize()][thing][0]
        
    def average(self, thing='dpp'):
        '''In the case of an element that has not been measured before, use
        the mean of what has been measured for all other elements.

        '''
        if thing not in ('dpp', 'visual', 'xrf', 'ratio', 'difference'):
            return (0,0)
        with open(self.json, 'r') as td:
            tdata = td.read()
        alltele = json.loads(tdata)
        a = []
        for el in alltele.keys():
            if 'dpp' in alltele[el]:
                a.append(alltele[el]['dpp'][0])

        result = numpy.array(a).mean()
        if str(result) == 'nan':
            result = 0.0
        return [result, numpy.array(a).std(), result, result]

    # def interpolate(self, energy):
    #     a = json.load(open(self.json))
    #     e, t = numpy.array([]), numpy.array([])
    #     for z in self.all_elements:
    #         if 'count' not in a[element_symbol(z)]:
    #             continue
    #         if a[element_symbol(z)]['count'] < self.reliability:
    #             continue
    #         t = numpy.append(t, a[element_symbol(z)]['dpp'][0])
    #         if z < 46:
    #             e=numpy.append(e, edge_energy(z, 'k'))
    #         else:
    #             e=numpy.append(e, edge_energy(z, 'l3'))
                             
    #     s=numpy.argsort(e)
    #     return(numpy.interp(energy, e[s], t[s]))

    def overhead_per_point(self, element, edge=None):
        a = json.load(open(self.json))
        element = element_symbol(element)
        if edge is not None and edge.lower() in ('l2', 'l1'):
            return(self.average(thing='dpp'))
        if element in a and 'dpp' in a[element]:
            return(a[element]['dpp'])
        else:
            if edge is None or edge.lower() not in ('l2', 'l1'):
                edge = 'k'
                if Z_number(element) > 45:
                    edge = 'l3'
            return(self.average(thing='dpp'))
        
