import matplotlib
import matplotlib.pyplot as plt
import numpy
import matplotlib.gridspec as gridspec
import time

from slack import img_to_slack

class GlancingAngle():
    fig = None
    ongoing = False
    iteration = 0
    spinner = 0
    filename = ''
    completed = False

    linear_uid = None
    inverted = False
    linear_motor = None
    linear_xaxis = []
    linear_data = []
    linear_best_fit = []
    linear_center = 0
    linear_amplitude = 0

    pitch_uid = None
    pitch_xaxis = []
    pitch_data = []
    pitch_center = 0
    pitch_amplitude = 0

    fluo_uid = None
    fluo_motor = None
    fluo_xaxis = []
    fluo_data = []
    fluo_center = 0
    fluo_amplitude = 0

    def start(self, **kwargs):
        self.ongoing = True
        self.fig = None
        self.filename = kwargs['filename']
        self.complete = False

    def stop(self):
        self.ongoing = False
        if self.complete is False:
            return
        
        if self.fig is not None:
            plt.close(self.fig.number)

        self.fig = plt.figure(tight_layout=True) #, figsize=(9,6))
        gs = gridspec.GridSpec(1,3)

        t  = self.fig.add_subplot(gs[0, 0])
        t.scatter(self.linear_xaxis, self.linear_data, color='blue')
        t.plot(self.linear_xaxis, self.linear_best_fit, color='red')
        t.scatter(self.linear_center, self.linear_amplitude/2, s=160, marker='x', color='green')
        t.set_facecolor((0.95, 0.95, 0.95))
        t.set_xlabel(f'{self.linear_motor} (mm)')
        t.set_ylabel(f'{self.inverted}It/I0 and error function')

        p  = self.fig.add_subplot(gs[0, 1])
        p.plot(self.pitch_xaxis, self.pitch_data, color='blue')
        p.scatter(self.pitch_center, self.pitch_amplitude, s=120, marker='x', color='green')
        p.set_facecolor((0.95, 0.95, 0.95))
        p.set_xlabel('xafs_pitch (deg)')
        p.set_ylabel('It/I0')
        p.set_title(f'alignment of spinner {self.spinner}')

        f = self.fig.add_subplot(gs[0, 2])
        f.plot(self.fluo_xaxis, self.fluo_data, color='blue')
        f.scatter(self.fluo_center, self.fluo_amplitude, s=120, marker='x', color='green')
        f.set_facecolor((0.95, 0.95, 0.95))
        f.set_xlabel(f'{self.fluo_motor} (mm)')
        f.set_ylabel('If/I0')

        self.fig.canvas.draw()
        self.fig.canvas.flush_events()
        
        if matplotlib.get_backend().lower() == 'agg':
            time.sleep(1)
            self.fig.savefig(self.filename)
            self.logger.info(f'saved spinner alignment figure {self.filename}')
            img_to_slack(self.filename, title=f'Alignment of spinner {self.spinner}', measurement='xafs')



    ## the following are deprecated:
    def plot_linear(self, **kwargs):
        self.linear_uid       = uid       = kwargs['uid']
        self.linear_motor     = motor     = kwargs['motor']
        self.linear_center    = center    = kwargs['center']
        self.linear_amplitude = amplitude = kwargs['amplitude']
        self.inverted         = inverted  = kwargs['inverted']
        self.spinner          = spinner   = kwargs['spinner']
        self.linear_xaxis     = xaxis     = kwargs['xaxis']
        self.linear_data      = data      = kwargs['data']
        self.linear_best_fit  = best_fit  = kwargs['best_fit']

        direction = motor.split('_')[1]

        if self.fig is not None:
            plt.close(self.fig.number)
        self.fig = plt.figure()
        ax = self.fig.gca()
        ax.scatter(xaxis, data, color='blue')
        ax.plot(xaxis, best_fit, color='red')
        ax.scatter(center, amplitude/2, s=160, marker='x', color='green')
        ax.set_facecolor((0.95, 0.95, 0.95))
        ax.set_xlabel(f'{motor} (mm)')
        ax.set_ylabel(f'{inverted}It/I0 and error function')
        ax.set_title(f'fit to {direction} scan, spinner {spinner}, center={center:.3f}')
        self.fig.canvas.manager.show()
        self.fig.canvas.flush_events() 

    def plot_pitch(self, **kwargs):
        self.pitch_uid       = uid       = kwargs['uid']
        self.pitch_center    = center    = kwargs['center']
        self.pitch_amplitude = amplitude = kwargs['amplitude']
        self.spinner         = spinner   = kwargs['spinner']
        self.pitch_xaxis     = xaxis     = kwargs['xaxis']
        self.pitch_data      = data      = kwargs['data']
        
        if self.fig is not None:
            plt.close(self.fig.number)
        self.fig = plt.figure()
        ax = self.fig.gca()
        ax.plot(xaxis, data)
        ax.scatter(center, amplitude, s=160, marker='x', color='green')
        ax.set_facecolor((0.95, 0.95, 0.95))
        ax.set_xlabel('xafs_pitch (deg)')
        ax.set_ylabel('It/I0')
        ax.set_title(f'pitch scan, spinner {spinner}, center={center:.3f}')


    def plot_fluo(self, **kwargs):
        self.fluo_uid       = uid       = kwargs['uid']
        self.fluo_motor     = motor     = kwargs['motor']
        self.fluo_center    = center    = kwargs['center']
        self.fluo_amplitude = amplitude = kwargs['amplitude']
        self.spinner        = spinner   = kwargs['spinner']
        self.fluo_xaxis     = xaxis     = kwargs['xaxis']
        self.fluo_data      = data      = kwargs['data']

        direction = motor.split('_')[1]
        
        if self.fig is not None:
            plt.close(self.fig.number)
        self.fig = plt.figure()
        ax = self.fig.gca()
        ax.plot(xaxis, data)
        ax.scatter(center, amplitude, s=160, marker='x', color='green')
        ax.set_facecolor((0.95, 0.95, 0.95))
        ax.set_xlabel(f'{motor} (mm)')
        ax.set_ylabel('If/I0 ')
        ax.set_title(f'{direction} scan, spinner {spinner}, center={center:.3f}')
            
