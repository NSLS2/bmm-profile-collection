import os

from bmm_tools.tools.misc     import isfloat
from bmm_tools.tools.messages import *  # error_msg et al. + boxedtext
from bmm_tools.tools.physics  import *  # HBARC ktoe etok KTOE e2l

from BMM.user_ns.bmm          import kafka
import numpy

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

CS_BOUNDS     = [-200, -30, 15.3, '14k']
CS_STEPS      = [10, 0.5, '0.05k']
CS_TIMES      = [0.5, 0.5, '0.25k']
CS_MULTIPLIER = 0.72


def sanitize_step_scan_parameters(bounds, steps, times):
    '''Attempt to identify and flag/correct some common scan parameter mistakes.'''
    problem = False
    text = ''

    ############################################################################
    # bounds is one longer than steps/times, length of steps = length of times #
    ############################################################################
    if (len(bounds) - len(steps)) != 1:
        error_msg('\nbounds must have one more item than steps\n')
        error_msg('\tbounds = "%s"\n' % ' '.join(map(str, bounds)))
        error_msg('\tsteps = "%s"\n'  % ' '.join(map(str, steps)))
        problem = True
    if (len(bounds) - len(times)) != 1:
        error_msg('\nbounds must have one more item than times\n')
        error_msg('\tbounds = "%s"\n' % ' '.join(map(str, bounds)))
        error_msg('\ttimes = "%s"\n'  % ' '.join(map(str, times)))
        problem = True

    ############################
    # tests of boundary values #
    ############################
    for b in bounds:
        if not isfloat(b) and b[-1:].lower() == 'k':
            if not isfloat(b[:-1]):
                error_msg('\n%s is not a valid scan boundary value\n' % b)
                problem = True
        elif not isfloat(b):
            error_msg('\n%s is not a valid scan boundary value\n' % b)
            problem = True

        if not isfloat(b) and b[:1] == '-' and b[-1:].lower() == 'k':
            error_msg('\nNegative bounds must be energy-valued, not k-valued (%s)\n' % b) 
            problem = True
               
    #############################
    # tests of step size values #
    #############################
    for s in steps:
        if not isfloat(s) and s[-1:].lower() == 'k':
            if not isfloat(s[:-1]):
                error_msg('\n%s is not a valid scan step size value\n' % s)
                problem = True
            elif float(s[:-1]) < 0:
                error_msg('\nStep sizes cannot be negative (%s)\n' % s)
                problem = True
        elif not isfloat(s):
            error_msg('\n%s is not a valid scan step size value\n' % s)
            problem = True

        if isfloat(s) and float(s) < 0:
            error_msg('\nStep sizes cannot be negative (%s)\n' % s)
            problem = True
        elif isfloat(s) and float(s) <= 0.09:
            warning_msg('\n%s is a very small step size!\n' % s)
        elif not isfloat(s) and s[-1:].lower() == 'k' and isfloat(s[-1:]) and float(s[:-1]) < 0.01:
            warning_msg('\n%s is a very small step size!\n' % s)
            
                
    ####################################
    # tests of integration time values #
    ####################################
    for t in times:
        if not isfloat(t) and t[-1:].lower() == 'k':
            if not isfloat(t[:-1]):
                error_msg('\n%s is not a valid integration time value\n' % t)
                problem = True
            elif float(t[:-1]) < 0:
                error_msg('\nIntegration times cannot be negative (%s)\n' % t)
                problem = True
        elif not isfloat(t):
            error_msg('\n%s is not a valid integration time value\n' % t)
            problem = True

        if isfloat(t) and float(t) < 0:
            error_msg('\nIntegration times cannot be negative (%s)\n' % t)
            problem = True
        elif isfloat(t) and float(t) <= 0.1:
            warning_msg('\n%s is a very short integration time!\n' % t)
        elif not isfloat(t) and t[-1:].lower() == 'k' and isfloat(t[-1:]) and float(t[:-1]) < 0.05:
            warning_msg('\n%s is a very short integration time!\n' % t)

    
            
    reference = 'https://nsls2.github.io/bmm-beamline-manual/xafs.html#scan-regions\n'

    return problem, text, reference
    


def conventional_grid(bounds=CS_BOUNDS, steps=CS_STEPS, times=CS_TIMES, e0=7112, element=None, edge=None, ththth=False):
    '''
    Parameters
    ----------
    bounds : list of float or str
        N relative energy values denoting the region boundaries of the step scan
    steps : list of float or str
        N-1 energy step sizes
    times : list of float or str
        N-1 integration time values
    e0 : float
        edge energy, reference for boundary values
    ththth : Boolean
        using the Si(333) reflection

    Output
    ------
    grid : list
        absolute energy values
    timegrid : list
        integration times
    approximate_time : float
        a very crude estimate of how long in minutes the scan will take
    delta : float
        uncertainty in execution time (if available)

    Boundary values are either in eV units or wavenumber units.
    Values in eV units are floats, wavenumber units are strings of the
    form '0.5k' or '1k'.  String valued boundaries indicate a value to
    be converted from wavenumber to energy.  E.g. '14k' will be
    converted to 746.75 eV, i.e. that much above the edge energy.

    Step values are either in eV units (floats) or wavenumber units
    (strings).  Again, wavenumber values will be converted to energy
    steps as appropriate.  For example, '0.05k' will be converted into
    energy steps such that the steps are constant 0.05 invAng in
    wavenumber.

    Time values are either in units of seconds (floats) or strings.
    If strings, the integration time will be a multiple of the
    wavenumber value of the energy point.  For example, '0.5k' says to
    integrate for a number of seconds equal to half the wavenumber.
    So at 5 invAng, integrate for 2.5 seconds.  At 10 invAng,
    integrate for 5 seconds.

    Examples
    --------
    this is the default (same as (g,it,at) = conventional_grid()):

    >>> (grid, inttime, time) = conventional_grid(bounds=[-200, -30, 15.3, '14k'],
    >>>                                           steps=[10, 0.5, '0.05k'],
    >>>                                           times=[0.5, 0.5, '0.25k'], e0=7112)

    more regions

    >>> (grid, inttime, time) = conventional_grid(bounds=[-200.0, -20.0, 30.0, '5k', '14.5k'],
    >>>                                           steps=[10.0, 0.5, 2, '0.05k'],
    >>>                                           times=[1, 1, 1, '1k'], e0=7112)

    many regions, energy boundaries, k-steps

    >>> (grid, inttime, time) = conventional_grid(bounds=[-200, -30, -10, 15, 100, 300, 500, 700, 900],
    >>>                                           steps=[10, 2, 0.5, '0.05k', '0.05k', '0.05k', '0.05k', '0.05k'],
    >>>                                           times=[0.5, 0.5, 0.5, 1, 2, 3, 4, 5], e0=7112)

    a one-region xanes scan

    >>> (grid, inttime, time) = conventional_grid(bounds=[-10, 40],
    >>>                                           steps=[0.25,],
    >>>                                           times=[0.5,], e0=7112)
    '''
    tele = user_ns['tele']
    
    if (len(bounds) - len(steps)) != 1:
        return (None, None, None, None)
    if (len(bounds) - len(times)) != 1:
        return (None, None, None, None)
    for i,s in enumerate(bounds):
        if type(s) is str:
            this = float(s[:-1])
            bounds[i] = ktoe(this)
    bounds.sort()

    enot = e0
    if ththth:
        enot = e0/3.0
        bounds = list(numpy.array(bounds)/3)
    grid = list()
    timegrid = list()
    for i,s in enumerate(steps):
        if type(s) is str:
            step = float(s[:-1])
            if ththth: step = step/3.
            ar = enot + ktoe(numpy.arange(etok(bounds[i]), etok(bounds[i+1]), step))
        else:
            step = steps[i]
            if ththth: step = step/3.
            ar = numpy.arange(enot+bounds[i], enot+bounds[i+1], step)
        grid = grid + list(ar)
        grid = list(numpy.round(grid, decimals=2))
        if type(times[i]) is str:
            tar = etok(ar-enot)*float(times[i][:-1])
        else:
            tar = times[i]*numpy.ones(len(ar))
        timegrid = timegrid + list(tar)
        timegrid = list(numpy.round(timegrid, decimals=2))

    #if element in ('Kr', 'Hg', 'Pu', 'Th', 'Ru', 'Am'):
    #    overhead, uncertainty = tele.average()
    try:
        overhead, uncertainty, maxdpp, mindpp = tele.overhead_per_point(element) #, edge)
    except:
        overhead, uncertainty = tele.average()

    #approximate_time = (sum(timegrid) + float(len(timegrid))*overhead + user_ns['BMMuser'].tweak_xas_time) / 60.0
    approximate_time = (sum(timegrid) + (len(timegrid)*overhead)) / 60
    delta = float(len(timegrid))*uncertainty / 60.0
    return (grid, timegrid, approximate_time, delta)


def xrfat(uid, energy=-1, xrffile=None, add=True, only=None, xmax=1500):
    '''Examine an XRF spectrum measured during a fluorescence XAFS scan.  

    This extracts an array from the data stored in the HDF5 file
    recorded during an XAFS scan and plots it for the user.

    arguments
    =========
    uid : UID string
      The UID of the XAFS scan from which to extract the XRF spectrum

    energy : int or float
      The incident energy of the XRF spectrum. If energy is 0 the
      first data point will be displayed.  If energy is a negative
      integer, the point that many steps from the end of the scan will
      be used. If energy is a positive integer less than the length of
      the scan, the point that many steps from the start of the scan
      will be used.  Otherwise, the data point with energy closest in 
      value to the given energy will be displayed.

    xrffile : str
      The filename stub for an output XDI-style file containing the
      displayed XRF spectrum. This will be written into the XRF folder
      in the user's data folder.  If missing, the .xdi extension will
      be added.  This will not overwrite an existing file.

    add : bool 
      If True, plot the sum of detector channels, else plot each
      individual channel.

    only : int
      If 1, 2, 3, or 4, plot only that channel from the 4-element
      detector.  8 means to plot the sole channel of the single
      element detector.  This does not actually have to be specified
      for and XAFS measurement using the 1-element detector.  That the
      1-element was used will be gleaned from the scan metadata.

    xmax : float 
      The upper extent of the plot is the specified energy plus
      this value.

    '''
    if type(energy) is int or type(energy) is float:
        energy = [energy, ]
    if xrffile is not None:
        if not xrffile.endswith('.xrf'):
            xrffile = xrffile + '.xrf'
            xrffile = os.path.join(user_ns['BMMuser'].folder, 'XRF', xrffile)
        if os.path.isfile(xrffile):
            warning_msg(f'{xrffile} already exists.  The plot will be shown, but the file will not be written.')
            xrffile = None
    kafka.message({'xrfat': 'start',
                   'uid' : uid,
                   'energy' : energy,
                   'xrffile' : xrffile,
                   'add' : add,
                   'only' : only,
                   'xmax' : xmax, })
