
from bluesky.plan_stubs import mv

from numpy import array
from sympy import geometry

from bmm_tools.tools.messages import whisper

from BMM.linescans     import linescan, prepare_alignment_scan, fetch_peak_position_via_redis
from BMM.resting_state import resting_state_plan
from BMM.user_ns.bmm   import kafka

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from rich import print as cprint


class Wafer():
    '''Simple class for supporting measurements on round wafer samples.

    With 

       wafer = Wafer()

    you can do the following.

    Move to any point near the edge of the wafer, then do

       RE(wafer.edge())

    assuming the scan and its fit look OK, do

       wafer.push()

    Repeat this at two more spots on the edge of the wafer.  Spots
    near the NE, NW, and SW edges of the wafer will give good results
    with low uncertainty.

    Once three spots have been found, do

       wafer.find_center()

    This uses sympy.geometry to find the center position and diameter
    of the wafer.  Move to the wafer center with

       RE(wafer.goto_center())

    Clear the results to find the center anew

       wafer.clear()

    '''
    points   = []
    center   = []
    diameter = 0
    out      = None
    
    def clear(self):
        self.points = []
    
    def push(self):
        xafs_x, xafs_y = user_ns['xafs_x'], user_ns['xafs_y']
        self.points.append([xafs_x.position, xafs_y.position])
        print(f'wafer.points is {self.points}')

    def find_center(self):
        tri = geometry.Triangle(*self.points)
        self.center = array(tri.circumcenter, dtype=float)
        self.diameter= array(tri.circumradius, dtype=float)*2
        print(f'The center is at {self.center}.   The diameter is {self.diameter}.')

    def goto_center(self):
        xafs_x, xafs_y = user_ns['xafs_x'], user_ns['xafs_y']
        yield from (mv(xafs_x, self.center[0], xafs_y, self.center[1]))

    def plot(self):
        #whisper(self.out.fit_report(min_correl=0))
        self.out.plot()
        
        
    def edge(self, motor='x'):
        '''Fit an error function to the linear scan against It. Plot the
        result. Move to the centroid of the error function.'''
        if motor == 'x':
            motor = user_ns['xafs_linx']
        else:
            motor = user_ns['xafs_liny']
        yield from prepare_alignment_scan()
        uid = yield from linescan(motor, 'it', -2, 2, 41, dopluck=False)
        kafka.message({'close': 'last'})

        kafka.message({'stepfit'    : True,
                       'uid'        : 'last',
                       'motor_name' : motor.name,
                       'signal'     : 'It',
                       'choice'     : 'peak'})
        target = fetch_peak_position_via_redis()
        yield from mv(motor, target)
        
        # table  = user_ns['db'][-1].table()
        # yy     = table[motor.name]
        # signal = table['It']/table['I0']
        # if float(signal[2]) > list(signal)[-2] :
        #     ss     = -(signal - signal[2])
        # else:
        #     ss     = signal - signal[2]
        # mod    = StepModel(form='erf')
        # pars   = mod.guess(ss, x=array(yy))
        # self.out    = mod.fit(ss, pars, x=array(yy))
        # whisper(self.out.fit_report(min_correl=0))
        # target = self.out.params['center'].value
        # kafka.message({'wafer'     : 'edge',
        #                'motor'     : motor.name,
        #                'xaxis'     : list(yy),
        #                'data'      : list(ss),
        #                'best_fit'  : list(self.out.best_fit),
        #                'center'    : target,
        #                'amplitude' : self.out.params['amplitude'].value,
        #                'uid'       : uid})
                       
        # #self.out.plot()   # uncomment to plot directly rather than via BMM's kafka worker
        # yield from mv(motor, target)
        yield from resting_state_plan()
        print(f'\nEdge found at X={user_ns["xafs_x"].position} and Y={user_ns["xafs_y"].position}')
        cprint(f'do [yellow2]wafer.push()[/yellow2] to add this point to the list for finding the wafer circumcenter\n')
