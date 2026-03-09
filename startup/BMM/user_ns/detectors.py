
import os, json, socket, uuid

from bmm_tools.tools.messages import whisper, error_msg

from BMM.functions import run_report
from BMM.user_ns.base import RE, profile_configuration
from BMM.user_ns.bmm import BMMuser
from BMM.workspace import rkvs

from ophyd import EpicsSignal
from ophyd.sim import noisy_det

try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False


run_report(__file__, text='detectors and cameras')


with_anacam = profile_configuration.getboolean('cameras', 'anacam') # True
with_cam1   = profile_configuration.getboolean('cameras', 'usb1')   # True
with_cam2   = profile_configuration.getboolean('cameras', 'usb2')   # True
with_webcam = profile_configuration.getboolean('cameras', 'webcam') # True


from ophyd.scaler import EpicsScaler

class GonioStruck(EpicsScaler):
    def on(self):
        print('Turning {} on'.format(self.name))
        self.state.put(1)

    def off(self):
        print('Turning {} off'.format(self.name))
        self.state.put(0)

    def on_plan(self):
        yield from mv(self.state, 1)

    def off_plan(self):
        yield from mv(self.state, 0)


bicron = GonioStruck('XF:06BM-ES:1{Sclr:1}', name='bicron')
for i in list(range(1,33)):
    text = 'bicron.channels.chan%d.kind = \'omitted\'' % i
    exec(text)
bicron.channels.chan25.kind = 'hinted'
bicron.channels.chan26.kind = 'hinted'
bicron.channels.chan25.name = 'Bicron'
bicron.channels.chan26.name = 'APD'



## need to do something like this:
##    caput XF:06BM-BI{EM:1}EM180:Current3:MeanValue_RBV.PREC 7
## to get a sensible reporting precision from the Ix channels
def set_precision(pv, val):
    '''Set the precision reported in a BEC LiveTable to a sensible value.

    Note that this will likely take effect only when bsui is stopped
    and restarted.

    '''
    EpicsSignal(pv.pvname + ".PREC", name='').put(val)


#######################################################################################
#  _____ _      _____ _____ ___________ ________  ___ _____ _____ ___________  _____  #
# |  ___| |    |  ___/  __ \_   _| ___ \  _  |  \/  ||  ___|_   _|  ___| ___ \/  ___| #
# | |__ | |    | |__ | /  \/ | | | |_/ / | | | .  . || |__   | | | |__ | |_/ /\ `--.  #
# |  __|| |    |  __|| |     | | |    /| | | | |\/| ||  __|  | | |  __||    /  `--. \ #
# | |___| |____| |___| \__/\ | | | |\ \\ \_/ / |  | || |___  | | | |___| |\ \ /\__/ / #
# \____/\_____/\____/ \____/ \_/ \_| \_|\___/\_|  |_/\____/  \_/ \____/\_| \_|\____/  #
#######################################################################################

# It is important that the /name/ of the signal being used for I0 be 'I0',
# the signal for It be 'It', and Ir be 'Ir'.  That allows the kafka
# consumer and other Tiled clients to correctly interpret the columns of the
# data table.
#
# omitted signals should get names related to what they /could/ be, but are
# distinct from the main signal names.

run_report('\t'+'electrometer and ion chambers')
from BMM.electrometer import BMMQuadEM, BMMDualEM, dark_current, IntegratedIC

ION_CHAMBERS = []               # list of ion chambers in use, will be populated below

# configure signal chains for I0/It/Ir, configuration flags from BMM.user_ns.dwelltime
from BMM.user_ns.dwelltime import with_ic0, with_ic1, with_ic2, with_iy, with_pips

try:
    quadem1 = BMMQuadEM('XF:06BM-BI{EM:1}EM180:', name='quadem1')
    quadem1.enable_electrometer()
    whisper('\t\t\t'+'instantiated quadem1')

    ## using quadem for I0 detector?
    if with_ic0 is False:
        quadem1.I0.kind, quadem1.I0.name = 'hinted', 'I0'
    else:
        quadem1.I0.kind, quadem1.I0.name = 'omitted', 'I0q'

    ## using quadem for transmission detector?
    if with_ic1 is False:
        quadem1.It.kind, quadem1.It.name = 'hinted', 'It'
    else:
        quadem1.It.kind, quadem1.It.name = 'omitted', 'Itq'

    ## using quadem for reference detector?
    if with_ic2 is False:
        quadem1.Ir.kind, quadem1.Ir.name = 'hinted', 'Ir'
        rkvs.set('BMM:Ir', 'quadem')  # help cadashboard keep track of which signal chain is used for Ir
    else:
        quadem1.Ir.kind, quadem1.Ir.name = 'omitted', 'Irq'
        rkvs.set('BMM:Ir', 'ic2')

    ## Electron yield detector: active/inactive
    ## PIPS detector: active/inactive
    if with_iy is True:
        quadem1.Iy.kind, quadem1.Iy.name = 'hinted', 'Iy'
        rkvs.set('BMM:Iy', 1)
    elif with_pips is True:
        quadem1.Iy.kind, quadem1.Iy.name = 'hinted', 'Pips'
        rkvs.set('BMM:pips', 1)
    else:
        quadem1.Iy.kind, quadem1.Iy.name = 'omitted', 'Iy'
        rkvs.set('BMM:Iy', 0)
        rkvs.set('BMM:pips', 0)


    if with_iy is True or with_pips is True or with_ic0 is False or with_ic1 is False or with_ic2 is False:
        ION_CHAMBERS.append(quadem1)

    set_precision(quadem1.current1.mean_value, 3)
    toss = quadem1.I0.describe()    # this seems to be necessary for the BEC to use the correct precision
    set_precision(quadem1.current2.mean_value, 3)
    toss = quadem1.It.describe()
    set_precision(quadem1.current3.mean_value, 3)
    toss = quadem1.Ir.describe()
    set_precision(quadem1.current4.mean_value, 3)
    toss = quadem1.Iy.describe()

except:
    quadem1 = None
    whisper('\t\t\t'+'quadem is not available, carrying on anyway....')



# this test was written during a time in January 2026 when firewall
# rules were temporarily blocking access to black box quadems.  This
# test + the try-except block above keep things operating correctly
# even if the quadem is completely absent.
from BMM.workspace import wa, TAB
freakout = 0
if profile_configuration.getboolean('electrometers', 'quadem') is True and quadem1 is None:
    freakout = 1
if freakout == 1:
    error_msg(f'{TAB}*** Uh oh!  You have configured the quadem incorrectly.')
    print(f'{TAB}    You currently have electrometers.quadem set to True in')
    print(f'{TAB}    the BMM_configuration.ini file, but the QuadEM')
    print(f'{TAB}    could not be instatiated.')
    print('')
    print(f'{TAB}    You need to either diagnose the problem with the QuadEM or')
    print(f'{TAB}    set electrometers.quadem to False ... then restart bsui\n')
    whisper(f'{TAB}    (Now issuing a command that will fail and return to the command line.)\n')
    ## the next line is intended to trigger an immediate error and return to the IPython command line
    wa.put(1)

    


# try:                            # might not be in use
#     dualio = BMMDualEM('XF:06BM-BI{EM:3}EM180:', name='DualI0')
#     dualio.Ia.kind = 'hinted'
#     dualio.Ib.kind = 'hinted'
#     dualio.Ia.name = 'Ia'
#     dualio.Ib.name = 'Ib'
# except:
#     dualio = None


#######################################################################
## A note about PV nomenclature for the ion chambers:
##
## The prefixes are:
##   I0: XF:06BM-BI{IC:0}EM180:
##   It: XF:06BM-BI{IC:1}EM180:
##   Ir: XF:06BM-BI{IC:3}EM180:
##
## That's right, 2 got skipped. This was an error in provisioning the
## ion chambers.  It was easier to configure bsui correctly than
## to fix the functioning ion chamber.
#######################################################################

try:                            # might not be in use
    ic0 = IntegratedIC('XF:06BM-BI{IC:0}EM180:', name='Ic0')
    ic0.enable_electrometer()
    whisper('\t\t\t'+'instantiated ic0')
    if with_ic0 is False:
        ic0.Ia.kind, ic0.Ia.name = 'omitted', 'I0a'
    else:
        ic0.Ia.kind, ic0.Ia.name = 'hinted', 'I0'
        
    ic0.Ib.kind, ic0.Ib.name = 'omitted', 'I0b'
    set_precision(ic0.current1.mean_value, 3)
    toss = ic0.Ia.describe()
    set_precision(ic0.current2.mean_value, 3)
    toss = ic0.Ib.describe()
    if with_ic0:
        ION_CHAMBERS.append(ic0)
except Exception as E:
    print(E)
    whisper('\t\t\t'+'ic0 is not available, falling back to ophyd.sim.noisy_det')
    ic0 = noisy_det

try:                            # might not be in use
    ic1 = IntegratedIC('XF:06BM-BI{IC:1}EM180:', name='Ic1')
    ic1.enable_electrometer()
    whisper('\t\t\t'+'instantiated ic1')
    if with_ic1 is False:
        ic1.Ia.kind, ic1.Ia.name = 'omitted', 'Ita'
    else:
        ic1.Ia.kind, ic1.Ia.name = 'hinted', 'It'
    ic1.Ib.kind, ic1.Ib.name = 'omitted', 'Itb'
    set_precision(ic1.current1.mean_value, 3)
    toss = ic1.Ia.describe()
    set_precision(ic1.current2.mean_value, 3)
    toss = ic1.Ib.describe()
    if with_ic1:
        ION_CHAMBERS.append(ic1)
except:    
    whisper('\t\t\t'+'ic1 is not available, falling back to ophyd.sim.noisy_det')
    ic1 = noisy_det


try:                            # might not be in use
    ic2 = IntegratedIC('XF:06BM-BI{IC:3}EM180:', name='Ic2')
    ic2.enable_electrometer()
    whisper('\t\t\t'+'instantiated ic2')
    if with_ic2 is False:
        ic2.Ia.kind, ic2.Ia.name = 'omitted', 'Ira'
    else:
        ic2.Ia.kind, ic2.Ia.name = 'hinted', 'Ir'
    ic2.Ib.kind, ic2.Ib.name = 'omitted', 'Irb'
    set_precision(ic2.current1.mean_value, 3)
    toss = ic2.Ia.describe()
    set_precision(ic2.current2.mean_value, 3)
    toss = ic2.Ib.describe()
    if with_ic2:
        ION_CHAMBERS.append(ic2)
except:    
    whisper('\t\t\t'+'ic2 is not available, falling back to ophyd.sim.noisy_det')
    ic2 = noisy_det


    
#set_precision(ic0.current1.mean_value, 3)
#toss = ic0.Ia.describe()
#set_precision(ic0.current2.mean_value, 3)
#toss = ic0.Ib.describe()
    
#quadem2 = BMMQuadEM('XF:06BM-BI{EM:2}EM180:', name='quadem2')


####################################################
#  _____   ___  ___  ___ ___________  ___   _____  #
# /  __ \ / _ \ |  \/  ||  ___| ___ \/ _ \ /  ___| #
# | /  \// /_\ \| .  . || |__ | |_/ / /_\ \\ `--.  #
# | |    |  _  || |\/| ||  __||    /|  _  | `--. \ #
# | \__/\| | | || |  | || |___| |\ \| | | |/\__/ / #
#  \____/\_| |_/\_|  |_/\____/\_| \_\_| |_/\____/  #
####################################################

run_report('\t'+'cameras')
from BMM.camera_device import BMMSnapshot, snap
from bmm_tools.devices.axis_webcam import AxisCaprotoCam
from BMM.user_ns.base import bmm_catalog

import bmm_tools.tools.db
bmm_tools.tools.db.bmm_catalog = bmm_catalog
file_resource = bmm_tools.tools.db.file_resource
show_snapshot = bmm_tools.tools.db.show_snapshot


# this root location is deprecated for the camera devices.  The _root
# of the camera device will be reset when the user configuration
# happens, so this initial configuration is harmless and transitory
run_report('\t\t'+'caproto IOCs for webcams')
temp_root = '/nsls2/data3/bmm/XAS/bucket'
xascam = AxisCaprotoCam("XF:06BM-ES{AxisCaproto:6}:", name="webcam-1",
                        root_dir=f"/nsls2/data3/bmm/proposals/{RE.md['cycle']}/{RE.md['data_session']}/assets")
xrdcam = AxisCaprotoCam("XF:06BM-ES{AxisCaproto:5}:", name="webcam-2",
                        root_dir=f"/nsls2/data3/bmm/proposals/{RE.md['cycle']}/{RE.md['data_session']}/assets")

thishost = socket.gethostname()
anacam = None
if is_re_worker_active() is False and 'ws3' in thishost:
    run_report('\t\t'+'initializing analog camera')
    anacam = BMMSnapshot(root=temp_root, which='analog', name='anacam')
    anacam.image.shape = (480, 640, 3)
    anacam.device = '/dev/v4l/by-id/usb-MACROSIL_AV_TO_USB2.0-video-index0'
    anacam.x, anacam.y = 640, 480    # width, height



run_report('\t\t'+'USB cameras: usb1, usb2')

from bmm_tools.devices.usb_camera import BMMUVCSingleTrigger
if with_cam1 is True:
    usb1 = BMMUVCSingleTrigger('XF:06BM-ES{UVC-Cam:1}', name="usbcam-1", read_attrs=["jpeg"])
else:
    usb1 = None

if with_cam2 is True:
    usb2 = BMMUVCSingleTrigger('XF:06BM-ES{UVC-Cam:2}', name="usbcam-2", read_attrs=["jpeg"])
else:
    usb2 = None

cam8 = BMMUVCSingleTrigger('XF:06BMB-BI{Cam:08}', name="cam-8", read_attrs=["jpeg"])


def display_last_image_usb_cam(catalog, camera=usb1):
    from PIL import Image
    print(catalog[-1]['primary']['data'][f'{camera.name}_image'])

    Image.fromarray(
        catalog[-1]['primary']['data'][f'{camera.name}_image'].read()[0]
    ).show()


###############################################
# ______ _____ _       ___ _____ _   _ _____  #
# | ___ \_   _| |     / _ \_   _| | | /  ___| #
# | |_/ / | | | |    / /_\ \| | | | | \ `--.  #
# |  __/  | | | |    |  _  || | | | | |`--. \ #
# | |    _| |_| |____| | | || | | |_| /\__/ / #
# \_|    \___/\_____/\_| |_/\_/  \___/\____/  #
###############################################

pilatus = None
pilatus_tiff = None


def prep_pilatus(pilatus):
    pilatus.cam_file_path.put('/disk2')
    pilatus.cam_file_number.put(1)
    pilatus.cam_auto_increment.put(1)
    tiff_filename = f"{RE.md['data_session']}_{RE.md['cycle']}"
    pilatus.cam_file_name.put(tiff_filename)
    pilatus.cam_file_format.put(0)
    pilatus.cam_file_template.put('%s%s_%3.3d.tiff')

    #tiff_filename = f"{RE.md['data_session']}_{RE.md['cycle']}"
    #EpicsSignal('XF:06BMB-ES{Det:PIL100k}:TIFF1:FileName', name='').put(tiff_filename)
    

from BMM.user_ns.dwelltime import with_pilatus
from BMM.user_ns.dcm import dcm
if with_pilatus is True:
    from bmm_tools.devices.pilatus import BMMPilatusSingleTrigger,  BMMPilatusTIFFSingleTrigger
    run_report('\t'+'Pilatus')

    pvbase = "XF:06BMB-ES{Det:PIL100k}:"

    
    ## make sure various plugins are turned on
    for x in ('image1', 'Pva1', 'HDF1', 'ROI1', 'ROI2', 'ROI3', 'ROI4', 'Stats1' , 'Stats2' , 'Stats3' , 'Stats4', 'ROIStat1', 'TIFF1'):
        EpicsSignal(f'{pvbase}{x}:EnableCallbacks', name='').put(1)
        # turn on all useful common plugins
        #    EpicsSignal('XF:06BMB-ES{Det:PIL100k}:image1:EnableCallbacks', name='').put(1)
        # and so on...

    
    pilatus = BMMPilatusSingleTrigger(pvbase, name="pilatus100k-1", read_attrs=["hdf5"])
    pilatus.stats.kind = "omitted"
    pilatus.stats.name = "total"
    pilatus.roi2.kind  = "hinted"
    pilatus.roi3.kind  = "hinted"
    pilatus.roi2.name  = "diffuse"
    pilatus.roi3.name  = "specular"
    #if pilatus.hdf5.run_time.get() == 0.0:
    pilatus.gain.put(0)         # 7-30KeV/Fast/LowG
    pilatus.photon_energy.put(dcm.energy.readback.get()/1000)
    pilatus.hdf5.stage_sigs['num_capture'] = 1
    pilatus.hdf5.warmup()

    ## starting ROI values
    roivalues = {'ROI2:MinX': 50,  'ROI2:SizeX': 50, 'ROI2:MinY': 50, 'ROI2:SizeY': 50,
                 'ROI3:MinX': 150, 'ROI3:SizeX': 50, 'ROI3:MinY': 50, 'ROI3:SizeY': 50, }
    for k,v in roivalues.items():
        EpicsSignal(f'{pvbase}{k}',  name='').put(v)
        EpicsSignal(f'{pvbase}{k.replace("ROI", "ROIStat1:")}',  name='').put(v)
        # this does 
        #   EpicsSignal('XF:06BMB-ES{Det:PIL100k}:ROI2:MinX',  name='').put(50)
        #   EpicsSignal('XF:06BMB-ES{Det:PIL100k}:ROIStat1:2:MinX',  name='').put(50)
        # and so on...
    EpicsSignal(f'{pvbase}ROIStat1:2:Use', name='').put(1)
    EpicsSignal(f'{pvbase}ROIStat1:3:Use', name='').put(1)
        
    #pilatus_tiff = BMMPilatusTIFFSingleTrigger("XF:06BMB-ES{Det:PIL100k}:", name="pilatus100k-1", read_attrs=["tiff"])
    #pilatus_tiff.stats.kind = "hinted"

    prep_pilatus(pilatus)




####################################
#  _____ _____ _____  ___________  #
# |  ___|_   _|  __ \|  ___| ___ \ #
# | |__   | | | |  \/| |__ | |_/ / #
# |  __|  | | | | __ |  __||    /  #
# | |___ _| |_| |_\ \| |___| |\ \  #
# \____/ \___/ \____/\____/\_| \_| #
####################################


eiger = None
    
from BMM.user_ns.dwelltime import with_eiger
if with_eiger is True:
    from BMM.eiger import BMMEigerSingleTrigger
    run_report('\t'+'Eiger')

    pvbase = "XF:06BM-ES{Det-Eiger:1}"
    ## make sure various plugins are turned on
    for x in ('image1', 'Pva1', 'HDF1', 'ROI1', 'ROI2', 'ROI3', 'ROI4', 'Stats1' , 'Stats2' , 'Stats3' , 'Stats4', 'ROIStat1',):
        EpicsSignal(f'{pvbase}{x}:EnableCallbacks', name='').put(1)
        # turn on all useful common plugins
        #    EpicsSignal('XF:06BM-ES{Det-Eiger:1}image1:EnableCallbacks', name='').put(1)
        # and so on...

        
    eiger = BMMEigerSingleTrigger(pvbase, name="eiger1m-1", read_attrs=["hdf5"])
    eiger.stats.kind = "omitted"
    eiger.roi2.kind  = "hinted"
    eiger.roi3.kind  = "hinted"
    eiger.roi2.name  = "diffuse"
    eiger.roi3.name  = "specular"
    if eiger.hdf5.run_time.get() < 0.1:
        eiger.hdf5.warmup()

    ## starting ROI values
    roivalues = {'ROI2:MinX': 501,  'ROI2:SizeX': 501, 'ROI2:MinY': 501, 'ROI2:SizeY': 501,
                 'ROI3:MinX': 1501, 'ROI3:SizeX': 501, 'ROI3:MinY': 501, 'ROI3:SizeY': 501, }
    for k,v in roivalues.items():
        EpicsSignal(f'{pvbase}{k}',  name='').put(v)
        EpicsSignal(f'{pvbase}{k.replace("ROI", "ROIStat1:")}',  name='').put(v)
        # this does 
        #   EpicsSignal('XF:06BM-ES{Det-Eiger:1}ROI2:MinX',  name='').put(50)
        #   EpicsSignal('XF:06BM-ES{Det-Eiger:1}ROIStat1:2:MinX',  name='').put(50)
        # and so on...
    #EpicsSignal(f'{pvbase}ROIStat1:2:Use', name='').put(1)
    #EpicsSignal(f'{pvbase}ROIStat1:3:Use', name='').put(1)

    eiger.hdf5.stage_sigs['num_capture'] = 1

    
####################################
# ______  ___   _   _ _____ _____  #
# |  _  \/ _ \ | \ | |_   _|  ___| #
# | | | / /_\ \|  \| | | | | |__   #
# | | | |  _  || . ` | | | |  __|  #
# | |/ /| | | || |\  | | | | |___  #
# |___/ \_| |_/\_| \_/ \_/ \____/  #
####################################


dante = None
from BMM.user_ns.dwelltime import with_dante

if with_dante is True:
    from BMM.dante import BMMDanteSingleTrigger
    run_report('\t'+'Dante')

    ## make sure various plugins are turned on
    EpicsSignal('XF:06BM-ES{Dante-Det:1}Pva1:EnableCallbacks',     name='').put(1)
    EpicsSignal('XF:06BM-ES{Dante-Det:1}HDF1:EnableCallbacks',     name='').put(1)
    EpicsSignal('XF:06BM-ES{Dante-Det:1}ROIStat1:EnableCallbacks', name='').put(1)

    # EpicsSignal('XF:06BM-ES{Dante-Det:1}ROI1:EnableCallbacks',   name='').put(1)
    # EpicsSignal('XF:06BM-ES{Dante-Det:1}ROI2:EnableCallbacks',   name='').put(1)
    # EpicsSignal('XF:06BM-ES{Dante-Det:1}ROI3:EnableCallbacks',   name='').put(1)
    # EpicsSignal('XF:06BM-ES{Dante-Det:1}ROI4:EnableCallbacks',   name='').put(1)

    # EpicsSignal('XF:06BM-ES{Dante-Det:1}Stats1:EnableCallbacks', name='').put(1)
    # EpicsSignal('XF:06BM-ES{Dante-Det:1}Stats2:EnableCallbacks', name='').put(1)
    # EpicsSignal('XF:06BM-ES{Dante-Det:1}Stats3:EnableCallbacks', name='').put(1)
    # EpicsSignal('XF:06BM-ES{Dante-Det:1}Stats4:EnableCallbacks', name='').put(1)
    
    dante = BMMDanteSingleTrigger("XF:06BM-ES{Dante-Det:1}", name="dante-1", read_attrs=["hdf5"])
    dante.cam.num_mca_channels.put(2)  # 4096 energy bins
    dante.cam.collect_mode.put(1)      # MCA Mapping mode
    #dante.hdf5.warmup()                # make sure HDF5 knows array sizes
    for i in range(1,8):               # detector is 7 element, even though Dante is 8 channel
        getattr(dante, f'roi{i}').kind = 'hinted'
    
#######################################################
# __   __ _________________ _____ _____ _____  _____  #
# \ \ / //  ___| ___ \ ___ \  ___/  ___/  ___||____ | #
#  \ V / \ `--.| |_/ / |_/ / |__ \ `--.\ `--.     / / #
#  /   \  `--. \  __/|    /|  __| `--. \`--. \    \ \ #
# / /^\ \/\__/ / |   | |\ \| |___/\__/ /\__/ /.___/ / #
# \/   \/\____/\_|   \_| \_\____/\____/\____/ \____/  #
#######################################################

# JL: debugging xspress3 IOC crash
from ophyd.log import config_ophyd_logging
import logging
#config_ophyd_logging(file="xspress3_ophyd_debug.log", level=logging.DEBUG)

xs  = None
xs4 = None
xs1 = None
xs7 = None

warmed_up = False

def _prep_xs(det):
    # This is necessary when the ioc restarts.  We trigger one image
    # for the hdf5 plugin to work correctly else, we get file writing
    # errors
    global warmed_up
    if warmed_up is False and det.hdf5.run_time.get() == 0.0:
        det.hdf5.warmup()
        warmed_up = True
    
    # Hints:
    for channel in det.iterate_channels():
        channel.kind = 'hinted'
        #channel.mcarois.kind = 'hinted'
        for mcaroi in channel.iterate_mcarois():
            mcaroi.total_rbv.kind = 'hinted'

    det.cam.configuration_attrs = ['acquire_period',
                                   'acquire_time',
                                   'image_mode',
                                   'manufacturer',
                                   'model',
                                   'num_exposures',
                                   'num_images',
                                   'temperature',
                                   'temperature_actual',
                                   'trigger_mode',
                                   'config_path',
                                   'config_save_path',
                                   'invert_f0',
                                   'invert_veto',
                                   'xsp_name',
                                   'num_channels',
                                   'num_frames_config',
                                   'run_flags',
                                   'trigger_signal']
    for channel in det.iterate_channels():
        mcaroi_names = list(channel.iterate_mcaroi_attr_names())
        for mcaroi in channel.iterate_mcarois():
            mcaroi.total_rbv.kind = 'omitted'

    det.set_rois()
    # hdf5folder = os.path.join('/nsls2', 'data', 'bmm', 'assets', 'xspress3', *BMMuser.date.split('-'))
    # xs4.hdf5.read_path_template = hdf5folder
    # xs4.hdf5.write_path_template = hdf5folder
    # xs4.hdf5.file_path.put(hdf5folder)

    for channel in det.iterate_channels():
        channel.xrf.dtype_str = "<f8"
        channel.get_external_file_ref().dtype_str = "<f8"

    ## This stage_sigs and trigger business was needed with the new (as of January 2023)
    ## to maintain the correct triggering state for our mode of operation here at BMM.
    ## Apparently to serve the needs of other BLs, the triggering mode would default
    ## back to "Software" at the end of a scan.  This overrides that behavior.
    det.cam.stage_sigs[det.cam.trigger_mode] = "Internal"

    
    
from BMM.user_ns.dwelltime import with_xspress3, use_4element, use_1element, use_7element
xs4_ioc, xs7_ioc = False, False

if with_xspress3 is True:
    run_report('\t'+'Xspress3')
    from BMM.xspress3 import xs_app_dir

    if 'xs3-4-1' in xs_app_dir.get():
        run_report('\t\t'+'The running XSpress3 IOC is for the 4-element SDD')
        xs4_ioc = True
        use_4element, use_7element = True, False
    elif 'xs3-7-1' in xs_app_dir.get():
        run_report('\t\t'+'The running XSpress3 IOC is for the 7-element SDD')
        xs7_ioc = True
        use_4element, use_7element = False, True


    if use_7element is True:
        run_report('\t'+'instantiate 7-element SDD')
        from BMM.xspress3_7element import BMMXspress3Detector_7Element
        xs7 = BMMXspress3Detector_7Element(prefix     = 'XF:06BM-ES{Xsp:1}:',
                                           name       = '7-element SDD',
                                           read_attrs = ['hdf5']    )
        _prep_xs(xs7)

    if use_1element is True:
        run_report('\t\t'+'instantiate 1-element SDD')
        from BMM.xspress3_1element import BMMXspress3Detector_1Element
        xs1 = BMMXspress3Detector_1Element(prefix     = 'XF:06BM-ES{Xsp:1}:',
                                           name       = '1-element SDD',
                                           read_attrs = ['hdf5']    )
        _prep_xs(xs1)

    if use_4element is True:
        run_report('\t\t'+'instantiate 4-element SDD')
        from BMM.xspress3_4element import BMMXspress3Detector_4Element
        xs4 = BMMXspress3Detector_4Element(prefix     = 'XF:06BM-ES{Xsp:1}:',
                                           name       = '4-element SDD',
                                           read_attrs = ['hdf5']    )
        _prep_xs(xs4)

def xspress3_set_detector(this=None):
    if this is None:
        if use_7element is True:
            this = 7
        elif use_4element is True:
            this = 4
        elif use_1element is True:
            this = 1
        #run_report('\t\t'+'"xs" is the 4-element detector')  # change to 7
        #rkvs.set('BMM:xspress3', 4)
        #return xs4
    if this == 4:
        run_report('\t\t'+'"xs" is the 4-element detector')
        rkvs.set('BMM:xspress3', 4)
        return xs4
    elif this == 1:
        run_report('\t\t'+'"xs" is the 1-element detector')
        rkvs.set('BMM:xspress3', 1)
        return xs1
    elif this == 7:
        run_report('\t\t'+'"xs" is the 7-element detector')
        rkvs.set('BMM:xspress3', 7)
        return xs7

primary = profile_configuration.getint('sdd', 'primary')  # primary SDD detector
if primary == 4 and 'xs3-7-1' in xs_app_dir.get():
    error_msg('\tYou have selected the 4-element detector as primary, but the 7-element IOC is running!')
    warning_msg('\tProceeding with the 7-element detector as primary.')
    primary = 7
elif primary == 7 and 'xs3-4-1' in xs_app_dir.get():
    error_msg('\tYou have selected the 7-element detector as primary, but the 4-element IOC is running!')
    warning_msg('\tProceeding with the 4-element detector as primary.')
    primary = 4
if with_xspress3 is True:
    if primary == 4:
        xs=xspress3_set_detector(4)
    elif primary == 7:
        xs=xspress3_set_detector(7)
    elif primary == 1:
        xs=xspress3_set_detector(1)
    else:
        xs=xspress3_set_detector(4)
        
#xs.get_external_file_ref().kind = "normal"
        
