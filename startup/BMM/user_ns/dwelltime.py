
import os

from BMM.functions import run_report
from BMM.user_ns.bmm import BMMuser
from BMM.user_ns.base import profile_configuration


run_report(__file__, text='dwelltime + selecting detectors for use')


################################################################################
# Configure detector selection here,
# False to exclude a detector from consideration in bsui

# Ion chambers
with_quadem    = profile_configuration['electrometers']['quadem'] # True            # available for Iy and other signals
with_iy        = profile_configuration['electrometers']['iy']     # False           # electron yield
with_pips      = profile_configuration['electrometers']['pips']   # False           # PIPS
with_diode     = profile_configuration['electrometers']['diode']  # False           # reflectivity PIN diode
with_ic0       = profile_configuration['electrometers']['ic0']    # True            # new I0 chamber
with_ic1       = profile_configuration['electrometers']['ic1']    # True            # new It chamber
with_ic2       = profile_configuration['electrometers']['ic2']    # True            # new Ir chamber
with_dualem    = profile_configuration['electrometers']['dualem'] # False           # deprecated, prototype

# fluorescence detectors and readout systems
with_struck    = False # profile_configuration['sdd']['struck']   # False           # deprecated OG fluorescence read out
with_xspress3  = profile_configuration['sdd']['xspress3'] # True
use_4element   = profile_configuration['sdd']['4element'] # True
use_1element   = profile_configuration['sdd']['1element'] # True
use_7element   = profile_configuration['sdd']['7element'] # False

# area detectors
with_pilatus   = profile_configuration['detectors']['pilatus'] # False
with_eiger     = profile_configuration['detectors']['eiger']   # False

# edxd through Dante
with_dante     = profile_configuration['detectors']['dante'] # False

def active_detectors_report():
    print(f'{with_quadem      = }')
    print(f'{with_ic0         = }')
    print(f'{with_ic1         = }')
    print(f'{with_ic2         = }')
    print(f'{with_xspress3    = }')
    print(u"\u2523" + u"\u2501" + f'{ use_7element  = }')
    print(u"\u2523" + u"\u2501" + f'{ use_4element  = }')
    print(u"\u2517" + u"\u2501" + f'{ use_1element  = }')
    print(f'{with_dante       = }')
    print(f'{with_pilatus     = }')
    print(f'{with_iy          = }')


################################################################################


##############################################################
# ______ _    _ _____ _      _    _____ ________  ___ _____  #
# |  _  \ |  | |  ___| |    | |  |_   _|_   _|  \/  ||  ___| #
# | | | | |  | | |__ | |    | |    | |   | | | .  . || |__   #
# | | | | |/\| |  __|| |    | |    | |   | | | |\/| ||  __|  #
# | |/ /\  /\  / |___| |____| |____| |  _| |_| |  | || |___  #
# |___/  \/  \/\____/\_____/\_____/\_/  \___/\_|  |_/\____/  #
##############################################################


# An error gets triggered during Azure CI testing that does not get triggered when
# running under IPython. This disables the Xspress3 during testing.
# This is a crude stopgap.
if os.environ.get('AZURE_TESTING'):
    with_xspress3, use_7element, use_4element, use_1element, with_pilatus, with_dante = False, False, False, False, False, False

if with_xspress3 is True:
    BMMuser.readout_mode = 'xspress3'
elif with_struck is True:
    BMMuser.readout_mode = 'analog'
else:
    BMMuser.readout_mode = None
    
from BMM.dwelltime import LockedDwellTimes

_locked_dwell_time = LockedDwellTimes('', name='dwti')
dwell_time = _locked_dwell_time.dwell_time
dwell_time.name = 'inttime'
