
try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False

# Top level bluesky plans to ensure available in the queueserver environment
from bluesky.plans import  count, scan
from bluesky.plan_stubs import mv, mvr

# basic Bluesky/Ophyd/Databroker functionality, deal with QS vs. bsui
from .base import *

# kafka worker (this is imported early -- MANY things rely on talking via kafka)
from .kafka import kafka

# define/initialize BMMuser, define ROI object
from .bmm import *

# stub for bluesky widgets
#from .bw import *

# define motor groups and individual motors, ancillary motor functionality
from .motors import *

# dcm motor group
from .dcm import *

# mirrors, slits, XAS sample & reference wheel, detector mount, actuators (shutters, flags)
# busy device, Linkam, Lakeshore, motor grid, kill switches
from .instruments import *

# limited goniometer support
#from .gonio import *

# Ring object, baseline definition
from .metadata import *

# tie together integration times of various detectors
from .dwelltime import *

# Struck (deprecated), electrometers, optical cameras, Pilatus, Xspress3 (4 channel & 1 channel)
from .detectors import *

# valves, temperatures, water flow, etc
from .utilities import *

# suspenders (this must be imported before bmm_end, which imports all the various experimental plans)
from .suspenders import suspenders

# everything else, read comments in that file
from .bmm_end import *

# some candy for bsui 
if not is_re_worker_active():
    print('\t', end='')
    get_ipython().run_line_magic("xmode", "Plain")
    from .prompt import *
    from .magic import *
    #import warnings
    #import databroker.mongo_normalized
    #warnings.filterwarnings("ignore", module='databroker.mongo_normalized')


#from BMM.user_ns.bmm import whoami
if BMMuser.trigger is True:     # provide feedback if importing persistent user information 
    if BMMuser.element is not None and with_xspress3 is True: # make sure Xspress3 is configured to measure from the correct ROI
        show_edges()
    print('')
    whoami()
    BMMuser.trigger = False
