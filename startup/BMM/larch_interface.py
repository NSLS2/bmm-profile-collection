
## Once upon a time Pallas hid a child, Erichthonius, born without a
## human mother, in a box made of Actaean osiers. She gave this to the
## three virgin daughters of two-natured Cecrops, who was part human
## part serpent, and ordered them not to pry into its secret. Hidden
## in the light leaves that grew thickly over an elm-tree I set out to
## watch what they might do. Two of the girls, Pandrosus and Herse,
## obeyed without cheating, but the third Aglauros called her sisters
## cowards and undid the knots with her hand, and inside they found a
## baby boy with a snake stretched out next to him. That act I
## betrayed to the goddess. And this is the reward I got for it, no
## longer consecrated to Minerva’s protection, and ranked below the
## Owl, that night-bird! My punishment should be a warning to all
## birds not to take risks by speaking out.’
##
##                           Ovid, Metamorphosis
##                           Book II:531-565

import numpy, os, sys

from larch import (Group, Parameter, isParameter, param_value, isNamedClass, Interpreter) 
from larch.xafs import (find_e0, pre_edge, autobk, xftf, xftr)
from larch.io import create_athena
import larch.utils.show as lus
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from larch.xray import atomic_symbol, xray_edge

#from BMM import user_ns as user_ns_module
#user_ns = vars(user_ns_module)

#from BMM.user_ns.bmm import BMMuser

LARCH = Interpreter()

## should import these from bmm_tools.toosl.physics
KTOE = 3.8099819442818976
def etok(ee):
    '''convert relative energy to wavenumber'''
    return numpy.sqrt(ee/KTOE)
def ktoe(k):
    '''convert wavenumber to relative energy'''
    return k*k*KTOE


class Pandrosus():
    '''A thin wrapper around basic XAS data processing for individual
    data sets as implemented in Larch.

    The plotting capabilities of this class are similar to the orange
    plot buttons in Athena.

    in bsui:
        this = Pandrosus()
        this.folder, this.db = BMMuser.folder, user_ns['db']
    That is, every time you make a Pandrosus object, you need to orient
    it correctly for reading and producing data for this session.

    Attributes
    ----------
    uid : str
        Databroker unique ID of the data set, used to fetch mu(E) from the database
    name : str
        Human-readable name of the data set
    group : str
        Larch group containing the data
    pre : dict
        Dictionary of pre-edge and normalization arguments
    bkg : dict
        Dictionary of background subtraction arguments
    fft : dict   Dictionary of forward Fourier transform arguments
    bft : dict
        Dictionary of backward Fourier transform arguments
    rmax : float
        upper bound of R-space plot

    See http://xraypy.github.io/xraylarch/xafs/preedge.html and
    http://xraypy.github.io/xraylarch/xafs/autobk.html for details
    about these parameters.

    Methods
    ------
    fetch:      
        Get data set and prepare for analysis with Larch
    make_xmu:
        method that actually constructs mu(E) from the data source
    put:
        make a Pandrosus object from ndarrays for energy and mu
    show:
        wrapper around Larch's show command, examine the content of the Larch group
    prep: 
        normalize, background subtract, and forward transform the data
    do_xftf:
        perform the forward (k->R) transform
    do_xftr:
        perform the reverse (R->q) transform

    Plotting methods
    ----------------
    plot_xmu :
        plot data in energy (alias = pe)
    plot_signals : 
        plot mu(E) with I0 and the transmission or fluorescence signal (allias = ps)
    plot_chik :
        plot data in k-space (alias = pk)
    plot_chir :
        plot data in R-space (alias = pr)
    plot_chiq :
        plot data in back-transform k-space (alias = pq)
    plot_chikq :
        plot chi(k) + RE(chi(q)) (alias = pkq)
    triplot :
        plot mu(E), chi(k), and chi(R) in a grid

    '''
    def __init__(self, uid=None, name=None):
        self.uid     = uid
        self.name    = name
        self.mode    = None
        self.element = None
        self.edge    = None
        self.group   = None
        self.title   = ''
        ## Larch parameters
        self.pre     = {'e0':None, 'pre1':None, 'pre2':None, 'norm1':None, 'norm2':None, 'nnorm':3, 'nvict':0,}
        self.bkg     = {'rbkg':1, 'e0':None, 'kmin':0, 'kmax':None, 'kweight':2,}
        self.fft     = {'window':'Hanning', 'kmin':3, 'kmax':12, 'dk':2,}
        self.bft     = {'window':'Hanning', 'rmin':1, 'rmax':3, 'dr':0.1,}
        ## plotting parameters
        self.xe      = 'energy (eV)'
        self.xk      = r'wavenumber ($\AA^{-1}$)'
        self.xr      = r'radial distance ($\AA$)'
        self.rmax    = 6

        ## some parameters to make this usable in bsui and not in bsui
        self.folder  = None
        self.db      = None
        self.reuse   = True

        self.facecolor = (1.0, 1.0, 1.0)

        self.verbose = False
        
        ## flow control parameters
        
    def make_xmu(self, uid, mode):
        '''Load energy and mu(E) arrays into Larch and into this wrapper object.
        
        ***************************************************************
        This should be the only part of this startup script that needs
        beamline-specific configuration.  What is shown below is
        specific to how data are retrieved from Databroker at
        BMM. Other beamlines -- or reading data from files -- will
        need something different.
        ***************************************************************

        Parameters
        ----------
        uid : str
            database identifier (assuming you are using databroker)
        mode : str
            'transmission', 'fluorescence', or 'reference'

        '''
        if 'v1' in str(type(self.db)):  # v1 databroker
            print('Pandrosus requires use of the Tiled interface.')
            print('The V1 interface to databroker is deprecated.')
            return
        else:                               # tiled catalog
            header = self.db[uid].metadata
            start = header['start']
            table  = self.db[uid].primary.read()
            
        self.group.energy = numpy.array(table['dcm_energy'])
        self.group.i0 = numpy.array(table['I0'])
        #if mode in ('flourescence', 'xs', 'xs1', 'fluo', 'flou', 'both'): mode = 'fluorescence'
        if mode == 'reference':
            self.group.mu = numpy.array(numpy.log(table['It']/table['Ir']))
            self.group.i0 = numpy.array(table['It'])
            self.group.signal = numpy.array(table['Ir'])

        #######################################################################################
        # CAUTION!!  This only works when BMMuser is correctly set.  This is unlikely to work #
        # on data in past history.  See new '_dtc' element of start document.  9 Sep 2020     #
        #######################################################################################
        elif mode in ('yield', 'fluo+yield'):
            self.group.mu = numpy.array(table['Iy']/table['I0'])
            self.group.i0 = numpy.array(table['I0'])
            self.group.signal = numpy.array(table['Iy'])

        elif mode in ('pips'):
            self.group.mu = numpy.array(table['Pips']/table['I0'])
            self.group.i0 = numpy.array(table['I0'])
            self.group.signal = numpy.array(table['Pips'])

        elif mode in ('fluorescence', 'flourescence', 'fluo', 'flou', 'xs', 'xs1', 'xs4', 'xs7', 'both'):
            columns = start['XDI']['_dtc']
            if self.verbose:
                print(columns)
            self.group.i0 = numpy.array(table['I0'])
            self.group.signal = numpy.zeros(len(self.group.i0))
            for col in columns:
                self.group.signal += numpy.array(table[col])
            self.group.mu = self.group.signal / self.group.i0

        elif mode in ('icit', 'ici0'):
            if mode == 'icit':
                self.group.mu = numpy.array(numpy.log(table['I0']/table['I0a']))
                self.group.i0 = numpy.array(table['I0'])
                self.group.signal = numpy.array(table['I0a'])
            elif mode == 'icit':
                self.group.mu = numpy.array(numpy.log(table['I0a']/table['It']))
                self.group.i0 = numpy.array(table['I0a'])
                self.group.signal = numpy.array(table['It'])

        # elif mode == 'xs1':
        #     columns = start['XDI']['_dtc']
        #     self.group.mu = numpy.array(table[columns[0]]/table['I0'])
        #     self.group.i0 = numpy.array(table['I0'])
        #     self.group.signal = numpy.array(table[columns[0]])

        # elif mode == 'xs':
        #     columns = start['XDI']['_dtc']
        #     self.group.mu = numpy.array((table[columns[0]]+table[columns[1]]+table[columns[2]]+table[columns[3]])/table['I0'])
        #     self.group.i0 = numpy.array(table['I0'])
        #     self.group.signal = numpy.array(table[columns[0]]+table[columns[1]]+table[columns[2]]+table[columns[3]])

        elif mode == 'ref':
            self.group.mu = numpy.array(numpy.log(table['It']/table['Ir']))
            self.group.i0 = numpy.array(table['It'])
            self.group.signal = numpy.array(table['Ir'])

        else:
            self.group.mu = numpy.array(numpy.log(table['I0']/table['It']))
            self.group.i0 = numpy.array(table['I0'])
            self.group.signal = numpy.array(table['It'])

        # why does self.group.signal get lost? weird....
        #print(self.group.signal)
        self.signal = self.group.signal


    def make_ref(self, uid):
        if 'v1' in str(type(self.db)):  # v1 databroker
            header = self.db[uid]
            start = header.start
            table  = header.table()
        else:                               # tiled catalog
            header = self.db[uid].metadata
            start = header['start']
            table  = self.db[uid].primary.read()
            
        self.group.energy = numpy.array(table['dcm_energy'])
        self.group.reference = numpy.array(numpy.log(table['It']/table['Ir']))

            
    def fetch(self, uid, name=None, mode='transmission', working_folder=None):
        self.uid = uid
        self.mode = mode
        if name is not None:
            self.name = name
        else:
            self.name = self.db[uid].metadata['start']['XDI']['_filename']
        self.group = Group(__name__=self.name)
        self.title = self.db[uid].metadata['start']['XDI']['Sample']['name']
        self.mode  = self.db[uid].metadata['start']['XDI']['_user']['mode']

        self.make_xmu(uid, mode=mode)
        self.make_ref(uid)
        self.prep()
        # self.group.args = {}
        # self.group.args['label'] = self.db[uid].metadata['start']['XDI']['_filename']
        # if working_folder is None:
        #     location = os.path.join(self.folder, 'prj', 'toss.prj')
        # else:
        #     location = os.path.join(self.workspace, 'prj', 'toss.prj')
        # toss = create_athena(location)
        # toss.add_group(self.group)
        # toss.save()
        # os.remove(location)
        # toss = None

            
    def put(self, energy, mu, name):
        self.name = name
        self.group = Group(__name__=self.name)
        self.group.energy = energy
        self.group.mu = mu
        self.prep()

    def find_edge(self):
        '''This re-implements the Demeter::Data::find_edge method
        '''
        diff = 100000
        for ed in ('K', 'l3', 'L2', 'L1'):
            for z in range(14, 98):  # larch.xray stops at Cf
                en = xray_edge(z, ed).energy
                this = abs(en - self.group.e0)
                if this > diff and en > self.group.e0:
                    break
                if this < diff:
                    diff = this
                    answer = z
                    edge = ed
        elem = atomic_symbol(answer)
        if (elem, edge) == ('Nd', 'L1'):    # Fe oxide
            (elem, edge) = ('Fe', 'K')
        elif (elem, edge) == ('Sm', 'L1'):  # Co oxide
            (elem, edge) = ('Co', 'K')
        elif (elem, edge) == ('Er', 'L1'):  # Ni oxide
            (elem, edge) = ('Ni', 'K')
        elif (elem, edge) == ('Ce', 'L1'):  # Mn oxide
            (elem, edge) = ('Mn', 'K')
        elif (elem, edge) == ('Ir', 'L1'):  # prefer Bi l3 to Ir L1
            (elem, edge) = ('Bi', 'L3')
        elif (elem, edge) == ('Th', 'L3'):  # prefer Se K to Tl L3
            (elem, edge) = ('Se', 'K')
        #elif (elem, edge) == ('W', 'L2'):   # prefer Pt L3 to W L2
        #    (elem, edge) = ('Pt', 'L3')
        elif (elem, edge) == ('Pb', 'L2'):  # prefer Se K to Tl L3
            (elem, edge) = ('Rb', 'K')
        #elif (elem, edge) == ('At', 'L1'):  # prefer Np L3 to At L1
        #    (elem, edge) = ('Np', 'L3')
        elif (elem, edge) == ('Ba', 'L1'):  # prefer Cr K to Ba L1
            (elem, edge) = ('Cr', 'K')
        #elif (elem, edge) == ('Er', 'L3'):  # prefer Ni K to Er L3
        #    (elem, edge) = ('Ni', 'K')
        elif (elem, edge) == ('Bk', 'L2'):  # prefer Pd K to Bk L2
            (elem, edge) = ('Pd', 'K')

        self.element = elem
        self.edge = edge
            
        
    def determine_kmax(self):
        if self.element is None or self.edge is None:
            return None
        if self.edge.lower() == 'k' or self.edge.lower() == 'l1':
            return None
        e0 = xray_edge(self.element, self.edge).energy
        if e0 is None:
            return None
        if self.edge.lower() == 'l3':
            l2 = xray_edge(self.element, 'L2').energy
            return etok(l2-e0-30)
        if self.edge.lower() == 'l1':
            l1 = xray_edge(self.element, 'L1').energy
            return etok(l1-e0-30)
        
        
        
    def prep(self):
        ## the next several lines seem necessary because the version
        ## of Larch currently at BMM is not correctly resolving
        ## pre1=pre2=None or norm1=norm2=None.  The following
        ## approximates Larch's defaults
        if self.pre['e0'] is None:
            find_e0(self.group.energy, mu=self.group.mu, group=self.group, _larch=LARCH)
            ezero = self.group.e0
        else:
            ezero = self.pre['e0']
        if self.pre['norm2'] is None:
            self.pre['norm2'] = self.group.energy.max() - ezero
        if self.pre['norm1'] is None:
            self.pre['norm1'] = self.pre['norm2'] / 5
        if self.pre['pre1'] is None:
            self.pre['pre1'] = self.group.energy.min() - ezero
        if self.pre['pre2'] is None:
            self.pre['pre2'] = self.pre['pre1'] / 3
        if self.element is None or self.edge is None:
            self.find_edge()
        if self.edge.lower() == 'l3':
            diff = xray_edge(self.element, 'L2').energy - xray_edge(self.element, 'L3').energy
            if self.pre['norm2'] > diff:
               self.pre['norm2'] = diff - 20 
        if self.edge.lower() == 'l2':
            diff = xray_edge(self.element, 'L1').energy - xray_edge(self.element, 'L2').energy
            if self.pre['norm2'] > diff:
               self.pre['norm2'] = diff - 20 
        pre_edge(self.group.energy, mu=self.group.mu, group=self.group,
                 e0    = ezero,
                 step  = None,
                 pre1  = self.pre['pre1'],
                 pre2  = self.pre['pre2'],
                 norm1 = self.pre['norm1'],
                 norm2 = self.pre['norm2'],
                 nnorm = self.pre['nnorm'],
                 nvict = self.pre['nvict'],
                 _larch=LARCH)
        if self.bkg['kmax'] is None:
            self.bkg['kmax'] = self.determine_kmax()
        autobk(self.group.energy, mu=self.group.mu, group=self.group,
               rbkg    = self.bkg['rbkg'],
               e0      = self.bkg['e0'],
               kmin    = self.bkg['kmin'],
               kmax    = self.bkg['kmax'],
               kweight = self.bkg['kweight'],
               _larch=LARCH)
        xftf(self.group.k, chi=self.group.chi, group=self.group,
             window  = self.fft['window'],
             kmin    = self.fft['kmin'],
             kmax    = self.fft['kmax'],
             dk      = self.fft['dk'],
             kweight = 2,
             _larch=LARCH)

    def show(self, which=None):
        if which is None:
            lus.show(self.group, _larch=LARCH)
        elif 'pre' in which:
            lus.show(self.group.pre_edge_details, _larch=LARCH)
        elif which == 'autobk':
            lus.show(self.group.autobk_details, _larch=LARCH)
        elif which == 'fft' or which == 'xftf':
            lus.show(self.group.xftf_details, _larch=LARCH)
        elif which == 'bft' or which == 'xftr':
            lus.show(self.group.xftr_details, _larch=LARCH)
        else:
            lus.show(self.group, _larch=LARCH)
        
    def plot_xmu(self, bkg=False, pre=False, post=False, norm=False, flat=False, deriv=False):
        '''Make a plot of mu(E) for a single data set.

        All arguments default to plot, so you must explicitly turn on
        each additional modification or trace beyond the raw data.

        Parameters
        ----------
        bkg : bool
            True to plot the background function
        pre : bool
            True to plot the pre-edge line
        post : bool
            True to plot the post-edge line
        norm : bool
            True to plot normalized mu(E)
        flat : bool
            True to plot "flattened" mu(E)
        deriv : bool
            True to plot the derivative of mu(E)

        Setting norm, flat, or deriv to True, forces bkg, pre, and post to False.

        Setting flat to True forces norm to False.

        Setting deriv to True forces all other arguments to false.
        '''
        fig = None
        if self.reuse is True:
            plt.cla()
        else:
            fig = plt.figure()
            fig.set_facecolor((0.9, 0.9, 0.9))
        plt.xlabel(self.xe)
        plt.title(self.name + ' in energy')
        plt.grid(which='major', axis='both')
        
        g = self.group

        if deriv is True:
            plt.ylabel(r'$d[\mu(E)] / dE$')
            plt.plot(g.energy, g.dmude, label=self.name + ' derivative')
            y = numpy.interp(g.e0, g.energy, g.dmude)
            plt.scatter(g.e0, y, marker='d', color='orchid')
        elif flat is True:
            plt.ylabel(r'flattened $\mu(E)$')
            plt.plot(g.energy, g.flat, label=self.name + ' flattened')
            y = numpy.interp(g.e0, g.energy, g.flat)
            plt.scatter(g.e0, y, marker='d', color='orchid')
        elif norm is True:
            plt.ylabel(r'normalized $\mu(E)$')
            plt.plot(g.energy, g.norm, label=self.name + ' normalized')
            y = numpy.interp(g.e0, g.energy, g.norm)
            plt.scatter(g.e0, y, marker='d', color='orchid')
        else:
            plt.ylabel(r'$\mu(E)$')
            if bkg is True:
                plt.plot(g.energy, g.bkg, label='background', color='C1')
                y = numpy.interp(g.e0, g.energy, g.mu)
                plt.scatter(g.e0, y, marker='d', color='orchid')
                y = numpy.interp(g.e0+ktoe(g.autobk_details.kmax), g.energy, g.mu)
                plt.scatter(g.e0+ktoe(g.autobk_details.kmax), y, marker=2, color='tan')
                
            plt.plot(g.energy, g.mu, label=r'$\mu(E)$', color='C0')
            if pre is True:
                plt.plot(g.energy, g.pre_edge, label='pre-edge', color='C2')
                y = numpy.interp(g.e0+g.pre_edge_details.pre1, g.energy, g.mu)
                plt.scatter(g.e0+g.pre_edge_details.pre1, y, marker='|', color='orchid')
                y = numpy.interp(g.e0+g.pre_edge_details.pre2, g.energy, g.mu)
                plt.scatter(g.e0+g.pre_edge_details.pre2, y, marker='|', color='orchid')
            if post is True:
                plt.plot(g.energy, g.post_edge, label='post-edge', color='C4')
                y = numpy.interp(g.e0+g.pre_edge_details.norm1, g.energy, g.mu)
                plt.scatter(g.e0+g.pre_edge_details.norm1, y, marker='|', color='orchid')
                y = numpy.interp(g.e0+g.pre_edge_details.norm2, g.energy, g.mu)
                plt.scatter(g.e0+g.pre_edge_details.norm2, y, marker='|', color='orchid')
        plt.legend(loc='best', shadow=True)
        if self.reuse is False:
            plt.close()
        if fig is not None:
            return fig

    def plot_signals(self):
        '''Make a plot of mu(E) for a single data set with I0 and the signal
        (i.e. either If or It) both scaled to plot on the same y-axis.

        This allows you to investigate the effect of monochromator
        glitches and other experimental artifacts.

        '''
        if self.reuse is True:
            plt.cla()
        else:
            fig = plt.figure()
            fig.set_facecolor((0.9, 0.9, 0.9))
        plt.xlabel(self.xe)
        plt.title(self.name + ' in energy')
        plt.grid(which='major', axis='both')

        g = self.group

        max_xmu    = g.mu.max()
        max_i0     = g.i0.max()
        max_signal = self.signal.max()

        plt.plot(g.energy, g.mu, label=r'$\mu(E)$')
        plt.plot(g.energy, g.i0*max_xmu/max_i0, label='$I_0$')
        plt.plot(g.energy, self.signal*max_xmu/max_signal, label='signal')
        plt.legend(loc='best', shadow=True)
        if self.reuse is False:
            plt.close()
            return fig

    def plot_reference(self):
        if self.reuse is True:
            plt.cla()
        else:
            fig = plt.figure()
            fig.set_facecolor((0.9, 0.9, 0.9))
            
        g = self.group
        plt.xlabel(self.xe)
        plt.title(self.name + ' in energy')
        plt.grid(which='major', axis='both')
        plt.ylabel(r'normalized $\mu(E)$')
        plt.plot(g.energy, g.flat, label=self.name + ' normalized')
        #y = numpy.interp(g.e0, g.energy, g.norm)
        #plt.scatter(g.e0, y, marker='d', color='orchid')

        b=Pandrosus()
        b.put(g.energy, g.reference, 'reference')
        b.prep()
        plt.plot(b.group.energy, b.group.flat, label='reference')

        legend = plt.legend(loc='best', shadow=True)
        if self.reuse is False:
            plt.close()
            return fig

        
    def plot_chi(self, kw=2, win=True):
        '''
        Make a plot in k-space of a single data set.

        Arguments:
          kw:   specify the k-weight used in the plot [2]
          win:  plot FT window if True [True]
        '''
        if self.reuse is True:
            plt.cla()
        else:
            fig = plt.figure()
            fig.set_facecolor((0.9, 0.9, 0.9))
        plt.xlabel(self.xk)
        plt.ylabel(rf"$k^{kw}\cdot\chi(k)$  ($\AA^{{-{kw}}}$)")
        plt.title(self.name + ' in k-space')
        plt.grid(which='major', axis='both')
        y = self.group.chi*self.group.k**kw
        plt.plot(self.group.k, y, label=r'$\chi(k)$')
        if win:
            plt.plot(self.group.k, self.group.kwin*y.max()*1.1, label='window', color='C8')
        #color_counter += 1
        legend = plt.legend(loc='best', shadow=True)
        if self.reuse is False:
            plt.close()
            return fig
    plot_chik = plot_chi
        
    def do_xftf(self, kw=2):
        xftf(self.group.k, chi=self.group.chi, group=self.group,
             window  = self.fft['window'],
             kmin    = self.fft['kmin'],
             kmax    = self.fft['kmax'],
             dk      = self.fft['dk'],
             kweight = kw,
             with_phase=True, _larch=LARCH)
    def plot_chir(self, kw=2, win=True, parts='m'):
        '''Make a plot in R-space of a single data set.

        Parameters
        ----------
        kw : int
            specify the k-weight used in the plot [2]
        win : bool
            plot backtransform/fitting window if True [True]
        parts : str
            specify the parts of the complex transform to plot ['m']

        The argument to parts is a string that can contain the letters
        'm', 'r', 'i', 'p', or 'e', indicating the magnitude, real part,
        imaginary part, phase, or envelope of the complex transform. Examples:

        Examples
        --------
        plot the magnitude and imaginary parts:

        >>> data.plot_chir(parts='mi')

        plot the envelope, real, and imaginary parts:

        >>> data.plot_chir(parts='eri')

        The order of the letters in the parts argument does not matter.

        '''
        self.do_xftf(kw=kw)
        self.do_xftr()
        if self.reuse is True:
            plt.cla()
        else:
            fig = plt.figure()
            fig.set_facecolor((0.9, 0.9, 0.9))

        plt.xlabel(self.xr)
        plt.title(self.name + ' in R-space')
        plt.grid(which='major', axis='both')
        y = self.group.chir_mag
        color_counter = 0
        ylabel = False
        if 'e' in parts.lower():
            plt.ylabel(rf"$|\chi(R)|$  ($\AA^{{-{kw+1}}}$)")
            ylabel = True
            plt.plot(self.group.r, self.group.chir_mag, label=r'Env[$\chi(R)$]', color='C%d'%color_counter)
            plt.plot(self.group.r, -1*self.group.chir_mag, color='C%d'%color_counter)
            color_counter += 1
        if 'm' in parts.lower():
            plt.ylabel(rf"$|\chi(R)|$  ($\AA^{{-{kw+1}}}$)")
            ylabel = True
            plt.plot(self.group.r, self.group.chir_mag, label=r'$|\chi(R)|$', color='C%d'%color_counter)
            color_counter += 1
        if 'r' in parts.lower():
            if not ylabel:
                plt.ylabel(rf"RE[$\chi$(R)]  ($\AA^{{-{kw+1}}}$)")
                ylabel = True
            plt.plot(self.group.r, self.group.chir_re, label=r'RE[$\chi(R)$]', color='C%d'%color_counter)
            color_counter += 1
        if 'i' in parts.lower():
            if not ylabel:
                plt.ylabel(rf"IM[$\chi$(R)]  ($\AA^{{-{kw+1}}}$)")
                ylabel = True
            plt.plot(self.group.r, self.group.chir_im, label=r'IM[$\chi(R)$]', color='C%d'%color_counter)
            color_counter += 1
        if 'p' in parts.lower():
            if not ylabel:
                plt.ylabel(r'Phase($\chi$(R))')
                ylabel = True
            plt.plot(self.group.r, self.group.chir_pha, label=r'Phase[$\chi(R)$]', color='C%d'%color_counter)
            color_counter += 1
        if win and parts.lower() != 'p':
            plt.plot(self.group.r, self.group.rwin*y.max()*1.1, label='window', color='C8')
        plt.xlim(right=self.rmax)
        plt.legend(loc='best', shadow=True)
        if self.reuse is False:
            plt.close()
            return fig
        
    def do_xftr(self):
        xftr(self.group.r, chir=self.group.chir, group=self.group,
             window = self.bft['window'],
             rmin   = self.bft['rmin'],
             rmax   = self.bft['rmax'],
             dr     = self.bft['dr'],
             with_phase=True, _larch=LARCH)
    def plot_chiq(self, kw=2, parts='r', win=True):
        '''Make a plot in back-transformed k-space of a single data set.

        Parameters
        ----------
        kw : int
            specify the k-weight used in the plot [2]
        win : bool
            plot FT window if True [True]
        parts : str 
            specify the parts of the complex transform to plot ['r']

        The argument to parts is a string that can contain the letters
        'm', 'r', 'i', 'p', or 'e', indicating the magnitude, real part,
        imaginary part, phase, or envelope of the complex transform. Examples:

        Examples
        --------
        plot the real parts (the part most like chi(k):
        
        >>> data.plot_chiq(parts='r')

        plot the envelope, real, and imaginary parts:

        >>> data.plot_chir(parts='eri')

        The order of the letters in the parts argument does not matter.

        '''
        self.prep()
        self.do_xftf(kw=kw)
        self.do_xftr()
        if self.reuse is True:
            plt.cla()
        else:
            fig = plt.figure()
            fig.set_facecolor((0.9, 0.9, 0.9))
        plt.xlabel(self.xk)
        plt.title(self.name + ' in back-transformed k-space')
        plt.xlim(right=self.group.k.max())
        plt.grid(which='major', axis='both')
        y = self.group.chiq_mag
        color_counter = 0
        ylabel = False
        if 'e' in parts.lower():
            plt.ylabel(rf"|$\chi$(q)|  ($\AA^{{-{kw}}}$)")
            ylabel = True
            plt.plot(self.group.q, self.group.chiq_mag, label=r'Env[$\chi(R)$]', color='C%d'%color_counter)
            plt.plot(self.group.q, -1*self.group.chiq_mag, color='C%d'%color_counter)
            color_counter += 1
        if 'm' in parts.lower():
            plt.ylabel(rf"|$\chi$(q)|  ($\AA^{{-{kw}}}$)")
            ylabel = True
            plt.plot(self.group.q, self.group.chiq_mag, label=r'$|\chi(R)|$', color='C%d'%color_counter)
            color_counter += 1
        if 'r' in parts.lower():
            if not ylabel:
                plt.ylabel(rf"RE[$\chi$(q)]  ($\AA^{{-{kw}}}$)")
                ylabel = True
            plt.plot(self.group.q, self.group.chiq_re, label=r'RE[$\chi(R)$]', color='C%d'%color_counter)
            color_counter += 1
        if 'i' in parts.lower():
            if not ylabel:
                plt.ylabel(rf"IM[$\chi$(q)]  ($\AA^{{-{kw}}}$)")
                ylabel = True
            plt.plot(self.group.q, self.group.chiq_im, label=r'IM[$\chi(R)$]', color='C%d'%color_counter)
            color_counter += 1
        if 'p' in parts.lower():
            if not ylabel:
                plt.ylabel(r'Phase($\chi$(q))')
                ylabel = True
            plt.plot(self.group.q, self.group.chiq_pha, label=r'Phase[$\chi(R)$]', color='C%d'%color_counter)
            color_counter += 1
        if win and parts.lower() != 'p':
            plt.plot(self.group.k, self.group.kwin*y.max()*1.1, label='window', color='C8')
        plt.legend(loc='best', shadow=True)

    def plot_chikq(self, kw=2, win=True):
        '''Make a plot in k-space with the real part of the Fourier filtered
        data of a single data set.

        Parameters
        ----------
        kw : int
            specify the k-weight used in the plot [2]
        win : bool
            plot FT window if True [True]
        '''
        self.do_xftf(kw=kw)
        self.do_xftr()
        if self.reuse is True:
            plt.cla()
        else:
            fig = plt.figure()
            fig.set_facecolor((0.9, 0.9, 0.9))
        plt.xlabel(r'wavenumber ($\AA^{-1}$)')
        plt.ylabel(r'$\chi$(k)')
        plt.title(self.name + ' in k-space and q-space')
        plt.xlim(right=self.group.k.max())
        plt.grid(which='major', axis='both')
        y = self.group.chi*self.group.k**kw
        plt.plot(self.group.k, y, label=self.name, color='C0')
        plt.plot(self.group.q, self.group.chiq_re, label='backtransform', color='C1')
        if win:
            plt.plot(self.group.k, self.group.kwin*y.max()*1.1, label='window', color='C8')
        plt.legend(loc='best', shadow=True)


    def triplot(self, kw=2):
        fig = plt.figure(tight_layout=True)
        gs = gridspec.GridSpec(2,2)

        self.prep()
        self.do_xftf(kw=kw)

        mu = fig.add_subplot(gs[0, :], facecolor=self.facecolor)
        mu.plot(self.group.energy, self.group.mu, label=r'$\mu(E)$', color='C0')
        mu.set_title(self.title)
        mu.set_ylabel(r'$\mu(E)$')
        if self.mode == 'icit':
            mu.set_ylabel(r'$\mu(E)$ (It is new IC)')
        elif self.mode == 'ici0':
            mu.set_ylabel(r'$\mu(E)$ (I0 is new IC)')
        mu.set_xlabel('energy (eV)')

        chik = fig.add_subplot(gs[1, 0], facecolor=self.facecolor)
        #chik.set_xlim(left=0)
        y = self.group.chi*self.group.k**kw
        chik.plot(self.group.k, y, label=r'$\chi(k)$', color='C0')
        chik.set_ylabel(rf'$\chi(k)$  ($\AA^{{-{kw}}}$)')
        chik.set_xlabel(r'wavenumber ($\AA^{-1}$)')

        chir = fig.add_subplot(gs[1, 1], facecolor=self.facecolor)
        chir.set_xlim(0,6)
        chir.plot(self.group.r, self.group.chir_mag, label=r'$|\chi(R)|$', color='C0')
        chir.set_ylabel(rf"$|\chi(R)|$  ($\AA^{{-{kw+1}}}$)")
        chir.set_xlabel(r'radial distance ($\AA$)')

        fig.align_labels()
        if matplotlib.get_backend().lower() != 'agg':
            #plt.show()
            fig.canvas.manager.show()
            fig.canvas.flush_events()
        return fig 
        
    pe  = plot_xmu
    ps  = plot_signals
    pk  = plot_chi
    pr  = plot_chir
    pq  = plot_chiq
    pkq = plot_chikq


from collections.abc import Iterable
## grouping of Pandrosus objects for making purple plots
class Kekropidai():
    '''Simple wrapper around a group of Pandrosus objects to facilitate
    multiple data set plots in the manner of Athena's purple plot
    buttons.

    Attributes
    ----------
    groups : list of str
        list of Pandrosus groups for plotting
    name : str
        name of this collection
    rmax : float
        upper bound of R-space plot
      

    Methods
    -------
    add :
        add a single group or a list of groups to the Kekropidai object
    put :
        make a Kekropidai object out of a list of UIDs
    merge :
        merge the contents of the Kekropidai object and return a
        Pandrosus object containing the merge
    plot_xmu : 
        (alias = pe) overplot all the groups in energy
    plot_i0 :
        (alias = p0) overplot I0 for all the groups
    plot_chi :
        (alias = pk) overplot all the groups in k-space
    plot_chir :
        (alias = pr) overplot all the groups in R-space
    plot_chiq : 
        (alias = pq) overplot all the groups in q-space

    Examples
    --------

    >>> bunch = Kekropidai(name='My data')
    >>> bunch.add(data_set1)
    >>> bunch.add(data_set2)
    >>> bunch.add(data_set3)
    >>> bunch.plot_xmu()
    '''
    def __init__(self, name=None):
        self.groups = list()
        self.name   = name
        self.rmax   = 6
        self.folder = None
        self.db     = None

    def put(self, uidlist):
        for u in uidlist:
            this = Pandrosus()
            this.folder, this.db = self.folder, self.db
            this.fetch(u)
            self.add(this)

    def merge(self):
        base = self.groups[0]
        ee = base.group.energy
        mm = base.group.mu
        for spectrum in self.groups[1:]:
            mu = numpy.interp(ee, spectrum.group.energy, spectrum.group.mu)
            mm = mm + mu
        mm = mm / len(self.groups)
        merge = Pandrosus()
        merge.folder, merge.db = self.folder, self.db
        merge.put(ee, mm, 'merge')
        return(merge)
        
            
    def add(self, groups):
        if 'Pandrosus' in str(type(groups)):
            # this is a single group
            self.groups.append(groups)
            return()
        if isinstance(groups, Iterable):
            for item in groups:
                if 'Pandrosus' in str(type(item)):
                    self.groups.append(item)

    def plot_xmu(self, norm=False, flat=False, deriv=False):
        '''Overplot multiple data sets in energy.

        The default is to plot all data as raw mu(E).

        Parameters
        ----------
        norm :  bool
            plot normalized data [False]
        flat :  bool
            plot flattened data [False]
        deriv : bool
            plot derivative data [False]

        Setting deriv to True turns off flat and norm.

        Setting flat to True turns off norm.

        '''
        plt.cla()
        plt.xlabel(self.groups[0].xe)
        plt.ylabel(r'$\mu(E)$')
        plt.grid(which='major', axis='both')
        title = r'$\mu$(E)'
        if self.name is not None:
            title = self.name
        if deriv is True:
            plt.title(f"Derivative of {title}")
        elif flat is True:
            plt.title(f"Flattened {title}")
        elif flat is True:
            plt.title(f"Normalized {title}")
        else:
            plt.title(f"{title}")
        for g in self.groups:
            g.prep()
            if deriv is True:
                plt.plot(g.group.energy, g.group.dmude, label=g.name)
            elif flat is True:
                plt.plot(g.group.energy, g.group.flat, label=g.name)
            elif norm is True:
                plt.plot(g.group.energy, g.group.norm, label=g.name)
            else:
                plt.plot(g.group.energy, g.group.mu, label=g.name)
        plt.legend(loc='best', shadow=True)

    def plot_i0(self):
        '''Overplot I0 in energy.
        '''
        plt.cla()
        plt.xlabel(self.groups[0].xe)
        plt.ylabel('$I_0$  (nA)')
        plt.grid(which='major', axis='both')
        plt.title('$I_0$')
        for g in self.groups:
            plt.plot(g.group.energy, g.group.i0, label=g.name)

        
        
    def plot_chi(self, kw=2, part=None):
        '''Overplot multiple data sets in k-space.

        Parameters
        ----------
        kw : int
            the k-weight to use for all plots [2]

        (The "part" argument is ignored.  It is there as a command
        line convenience.)

        '''
        plt.cla()
        plt.xlabel(self.groups[0].xk)
        plt.ylabel(rf"$k^{{{kw}}}\cdot\chi(k)$  ($\AA^{{-{kw}}}$)")
        plt.grid(which='major', axis='both')
        title = 'EXAFS data'
        if self.name is not None:
            title = self.name + ' in k-space'
        plt.title(f"{title}")
        for g in self.groups:
            g.prep()
            y = g.group.chi*g.group.k**kw
            plt.plot(g.group.k, y, label=g.name)
        plt.legend(loc='best', shadow=True)
    plot_chik = plot_chi

    def plot_chir(self, kw=2, part='m'):
        '''Overplot multiple data sets in R-space.

        Parameters
        ----------
        kw : int
            the k-weight to use in all Fourier transforms [2]
        part : str
            the part of the complex FT to plot ['m']
            
        m = magnitude
        r = real part
        m = imaginary part
        p = phase
        '''
        plt.cla()
        plt.xlabel(self.groups[0].xr)
        plt.grid(which='major', axis='both')
        title = 'FT data'
        if self.name is not None:
            title = self.name + ' in R-space'
        if part.lower() == 'r':
            plt.title(f"Real part of {title}")
            plt.ylabel(rf"RE[$\chi$(R)]  ($\AA^{{-{kw+1}}}$)")
        elif part.lower() == 'i':
            plt.title(f"Imaginary part of {title}")
            plt.ylabel(rf"Im[$\chi$(R)]  ($\AA^{{-{kw+1}}}$)")
        elif part.lower() == 'p':
            plt.title(f"Phase of {title}")
            plt.ylabel(rf"Phase[$\chi$(R)]")
        else:
            plt.title(f"Magnitude of {title}")
            plt.ylabel(rf"|$\chi$(R)|  ($\AA^{{-{kw+1}}}$)")
            
        for g in self.groups:
            g.prep()
            g.do_xftf(kw=kw)
            if part.lower() == 'r':
                plt.plot(g.group.r, g.group.chir_re,  label=g.name)
            elif part.lower() == 'i':
                plt.plot(g.group.r, g.group.chir_im,  label=g.name)
            elif part.lower() == 'p':
                plt.plot(g.group.r, g.group.chir_pha, label=g.name)
            else:
                plt.plot(g.group.r, g.group.chir_mag, label=g.name)

        plt.xlim(right=self.rmax)
        plt.legend(loc='best', shadow=True)

    def plot_chiq(self, kw=2, part='r'):
        '''Overplot multiple data sets in backtransformed k-space.

        Parameters
        ----------
        kw : int
            the k-weight to use in all Fourier transforms [2]
        part : str
            the part of the complex FT to plot ['m']
            
        m = magnitude
        r = real part
        m = imaginary part
        p = phase

        '''
        plt.cla()
        plt.xlabel(self.groups[0].xk)
        plt.grid(which='major', axis='both')
        maxk = 0
        title = 'back-transformed data'
        if self.name is not None:
            title = self.name + ' in q-space'
        if part.lower() == 'r':
            plt.title(f"Real part of {title}")
            plt.ylabel(rf"RE[$\chi$(q)]  ($\AA^{{-{kw}}}$)")
        elif part.lower() == 'i':
            plt.title(f"Imaginary part of {title}")
            plt.ylabel(rf"Im[$\chi$(q)]  ($\AA^{{-{kw}}}$)")
        elif part.lower() == 'p':
            plt.title(f"Phase of {title}")
            plt.ylabel(r'Phase[$\chi$(q)]')
        else:
            plt.title(f"Magnitude of {title}")
            plt.ylabel(rf"|$\chi$(q)|  ($\AA^{{-{kw}}}$)")
            
        for g in self.groups:
            g.prep()
            g.do_xftf(kw=kw)
            g.do_xftr()
            maxk = max([maxk, g.group.k.max()])
            if part.lower() == 'r':
                plt.plot(g.group.q, g.group.chiq_re,  label=g.name)
            elif part.lower() == 'i':
                plt.plot(g.group.q, g.group.chiq_im,  label=g.name)
            elif part.lower() == 'p':
                plt.plot(g.group.q, g.group.chiq_pha, label=g.name)
            else:
                plt.plot(g.group.q, g.group.chiq_mag, label=g.name)

        plt.xlim(right=maxk*1.1)
        plt.legend(loc='best', shadow=True)

    pe = plot_xmu
    p0 = plot_i0
    pk = plot_chi
    pr = plot_chir
    pq = plot_chiq

    
