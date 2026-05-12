    
from pathlib import PurePath
from itertools import count
from collections import deque, OrderedDict
import time as ttime
from tqdm import tqdm

from ophyd import Component as Cpt
from ophyd import EpicsSignal, EpicsSignalRO, EpicsSignalWithRBV
from ophyd.areadetector import AreaDetector, ImagePlugin
from ophyd.areadetector.cam import PilatusDetectorCam
from ophyd.areadetector.base import EpicsSignalWithRBV
from ophyd.areadetector.filestore_mixins import resource_factory, FileStoreHDF5, FileStoreTIFF, FileStoreIterativeWrite, FileStorePluginBase
from ophyd.areadetector.plugins import HDF5Plugin_V33, TIFFPlugin_V33, StatsPlugin_V33

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)
md = user_ns["RE"].md

from nslsii.ad33 import SingleTriggerV33
from ophyd import Component as C


from bmm_tools.tools.messages import *  # error_msg et al. + boxedtext
from BMM.user_ns.base import PROPOSALS

######################################################################################
# ______ _____ _       ___ _____ _   _ _____              ___   _____________ _____  #
# | ___ \_   _| |     / _ \_   _| | | /  ___|            / / | | |  _  \  ___|  ___| #
# | |_/ / | | | |    / /_\ \| | | | | \ `--.  __      __/ /| |_| | | | | |_  |___ \  #
# |  __/  | | | |    |  _  || | | | | |`--. \ \ \ /\ / / / |  _  | | | |  _|     \ \ #
# | |    _| |_| |____| | | || | | |_| /\__/ /  \ V  V / /  | | | | |/ /| |   /\__/ / #
# \_|    \___/\_____/\_| |_/\_/  \___/\____/    \_/\_/_/   \_| |_/___/ \_|   \____/  #
######################################################################################
                                                                                  
                                                                                


class BMMFileStoreHDF5(FileStorePluginBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filestore_spec = "AD_HDF5"  # spec name stored in resource doc
        self.stage_sigs.update(
            [
                ("file_template", "%s%s_%6.6d.h5"),
                ("file_write_mode", "Capture"),
                ("capture", 1),
                # TODO: Remove once num capture is updated elsewhere
                #("num_capture", 3)

            ]
        )        # 'Single' file_write_mode means one image : one file.
        # It does NOT mean that 'num_images' is ignored.

    def get_frames_per_point(self):
        return self.parent.cam.num_images.get()

    def stage(self):
        super().stage()
        # this over-rides the behavior from the base stage
        self._fn = self.file_template.get() % (
            self.file_path.get(),
            self.file_name.get(),
            # file_number is *next* iteration
            self.file_number.get(),
        )

        resource_kwargs = {
            "frame_per_point": self.get_frames_per_point(),
        }
        self._generate_resource(resource_kwargs)
    
class BMMHDF5Plugin(HDF5Plugin_V33, BMMFileStoreHDF5, FileStoreIterativeWrite):
    def warmup(self):
        """
        A convenience method for 'priming' the plugin.
        The plugin has to 'see' one acquisition before it is ready to capture.
        This sets the array size, etc.
        NOTE : this comes from:
            https://github.com/NSLS-II/ophyd/blob/main/ophyd/areadetector/plugins.py
        We had to replace "cam" with "settings" here.

        This has been slightly modified by Bruce to avoid a situation where the warmup
        hangs.  Also to add some indication on screen for what is happening.
        """
        whisper("                        warming up the Pilatus hdf5 plugin...")
        self.enable.set(1).wait()

        # JOSH: proposed changes for new IOC

        tm = 'Internal'         # trigger mode for Pilatus
        if 'eiger' in self.parent.name:
            tm = 'Internal Series'
        sigs = OrderedDict([(self.parent.cam.array_callbacks, 1),
                            (self.parent.cam.image_mode, "Single"),
                            (self.parent.cam.trigger_mode, tm),
                            # just in case the acquisition time is set very long...
                            (self.parent.cam.acquire_time, 0.2),
                            (self.parent.cam.num_images, 1),
                            #(self.parent.cam.acquire, 1)
                        ]
        )

        original_vals = {sig: sig.get() for sig in sigs}

        # Remove the hdf5.capture item here to avoid an error as it should reset back to 0 itself
        # del original_vals[self.capture]

        for sig, val in sigs.items():
            sig.set(val).wait()
            ttime.sleep(0.1)  # abundance of caution

        self.parent.cam.acquire.set(1).wait()
        
        # JOSH: do we need more than 2 seconds here?
        #       adding more time here helps!
        for i in tqdm(range(4), colour='#7f8c8d'):
            ttime.sleep(0.5)  # wait for acquisition

        for sig, val in reversed(list(original_vals.items())):
            ttime.sleep(0.1)
            sig.set(val).wait()
        whisper("                        done")

    




class BMMPilatus(AreaDetector):
    image = C(ImagePlugin, "image1:")
    hdf5 = C(
        BMMHDF5Plugin,
        "HDF1:",
        write_path_template=f"{PROPOSALS}/{md['cycle']}/{md['data_session']}/assets/pilatus100k-1/%Y/%m/%d/",
        read_path_template=f"{PROPOSALS}/{md['cycle']}/{md['data_session']}/assets/pilatus100k-1//%Y/%m/%d/",
        read_attrs=[],
        root=f"{PROPOSALS}/{md['cycle']}/{md['data_session']}/assets/pilatus100k-1/",
    )
    stats = C(EpicsSignalRO, "Stats1:Total_RBV")
    roi2  = C(EpicsSignalRO, "ROIStat1:2:Total_RBV", name = 'diffuse')
    roi3  = C(EpicsSignalRO, "ROIStat1:3:Total_RBV", name = 'specular')

    
    cam_file_path      = C(EpicsSignalWithRBV, 'cam1:FilePath')
    cam_file_name      = C(EpicsSignalWithRBV, 'cam1:FileName')
    cam_file_number    = C(EpicsSignalWithRBV, 'cam1:FileNumber')
    cam_auto_increment = C(EpicsSignalWithRBV, 'cam1:AutoIncrement')
    cam_file_template  = C(EpicsSignalWithRBV, 'cam1:FileTemplate')
    cam_full_file_name = C(EpicsSignalRO,      'cam1:FullFileName_RBV')
    cam_file_format    = C(EpicsSignalWithRBV, 'cam1:FileFormat')

    threshold_energy   = C(EpicsSignalWithRBV, 'cam1:ThresholdEnergy')
    photon_energy      = C(EpicsSignalWithRBV, 'cam1:Energy')
    gain               = C(EpicsSignal,        'cam1:GainMenu')
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        #self.stage_sigs.update([(self.cam.trigger_mode, "Internal")])

    def make_data_key(self):
        source = "PV:{}".format(self.prefix)
        # This shape is expected to match arr.shape for the array.
        shape = (
            1,
            self.cam.array_size.array_size_y.get(),
            self.cam.array_size.array_size_x.get(),
        )
        
        data_key = dict(
            shape=shape,
            source=source,
            dtype="array",
            dtype_str="<f4",
            external="FILESTORE:",
        )
        #print(data_key)
        return data_key


class BMMPilatusSingleTrigger(SingleTriggerV33, BMMPilatus):
    pass





######################################################################################
# ______ _____ _       ___ _____ _   _ _____              _______ _________________  #
# | ___ \_   _| |     / _ \_   _| | | /  ___|            / /_   _|_   _|  ___|  ___| #
# | |_/ / | | | |    / /_\ \| | | | | \ `--.  __      __/ /  | |   | | | |_  | |_    #
# |  __/  | | | |    |  _  || | | | | |`--. \ \ \ /\ / / /   | |   | | |  _| |  _|   #
# | |    _| |_| |____| | | || | | |_| /\__/ /  \ V  V / /    | |  _| |_| |   | |     #
# \_|    \___/\_____/\_| |_/\_/  \___/\____/    \_/\_/_/     \_/  \___/\_|   \_|     #
######################################################################################
                                                                                


class BMMFileStoreTIFF(FileStorePluginBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filestore_spec = "AD_TIFF"  # spec name stored in resource doc
        self.stage_sigs.update(
            [
                ("file_template", "%s%s_%6.6d.tiff"),
                ("file_write_mode", "Capture"),
                ("capture", 1),
                # TODO: Remove once num capture is updated elsewhere
                #("num_capture", 3)

            ]
        )
        # 'Single' file_write_mode means one image : one file.
        # It does NOT mean that 'num_images' is ignored.

    def get_frames_per_point(self):
        return self.parent.cam.num_images.get()

    def stage(self):
        super().stage()
        # this over-rides the behavior from the base stage
        self._fn = self.file_template.get() % (
            self.file_path.get(),
            self.file_name.get(),
            # file_number is *next* iteration
            self.file_number.get(),
        )

        resource_kwargs = {
            #"template": "%s%s_%6.6d.tiff",
            #"filename": self.file_name.get(),
            "frame_per_point": self.get_frames_per_point(),
            #"chunk_size": 1,
        }
        self._generate_resource(resource_kwargs)
    
class BMMTIFFPlugin(TIFFPlugin_V33, BMMFileStoreTIFF, FileStoreIterativeWrite):
    pass




class BMMPilatusTIFF(AreaDetector):
    image = C(ImagePlugin, "image1:")
    tiff = C(
        BMMTIFFPlugin,
        "TIFF1:",
        write_path_template=f"{PROPOSALS}/{md['cycle']}/{md['data_session']}/assets/pilatus100k-1/%Y/%m/%d/",
        read_path_template=f"{PROPOSALS}/{md['cycle']}/{md['data_session']}/assets/pilatus100k-1//%Y/%m/%d/",
        read_attrs=[],
        root=f"{PROPOSALS}/{md['cycle']}/{md['data_session']}/assets/pilatus100k-1/",
    )
    stats = C(EpicsSignalRO, "Stats1:Total_RBV")

    cam_file_path      = C(EpicsSignalWithRBV, 'cam1:FilePath')
    cam_file_name      = C(EpicsSignalWithRBV, 'cam1:FileName')
    cam_file_number    = C(EpicsSignalWithRBV, 'cam1:FileNumber')
    cam_auto_increment = C(EpicsSignalWithRBV, 'cam1:AutoIncrement')
    cam_file_template  = C(EpicsSignalWithRBV, 'cam1:FileTemplate')
    cam_full_file_name = C(EpicsSignalRO,      'cam1:FullFileName_RBV')
    cam_file_format    = C(EpicsSignalWithRBV, 'cam1:FileFormat')
    cam_file_template  = C(EpicsSignalWithRBV, 'cam1:FileTemplate')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        #self.stage_sigs.update([(self.cam.trigger_mode, "Internal")])

    def make_data_key(self):
        source = "PV:{}".format(self.prefix)
        # This shape is expected to match arr.shape for the array.
        shape = (
            self.cam.array_size.array_size_y.get(),
            self.cam.array_size.array_size_x.get(),
        )
        
        data_key = dict(
            shape=shape,
            source=source,
            dtype="array",
            dtype_str="<f4",
            external="FILESTORE:",
        )
        #print(data_key)
        return data_key


class BMMPilatusTIFFSingleTrigger(SingleTriggerV33, BMMPilatusTIFF):
    pass


