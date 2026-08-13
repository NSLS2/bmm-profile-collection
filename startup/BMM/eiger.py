    
from pathlib import PurePath
from itertools import count
from collections import deque, OrderedDict
import time as ttime
from tqdm import tqdm

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

from BMM.user_ns.base import PROPOSALS

from bmm_tools.tools.messages  import *  # error_msg et al. + boxedtext
from bmm_tools.devices.pilatus import BMMFileStoreHDF5, BMMHDF5Plugin

class BMMEiger(AreaDetector):
    #image = C(ImagePlugin, "image1:")
    hdf5 = C(
        BMMHDF5Plugin,
        "HDF1:",
        write_path_template=f"{PROPOSALS}/{md['cycle']}/{md['data_session']}/assets/eiger1m-1/%Y/%m/%d/",
        read_path_template=f"{PROPOSALS}/{md['cycle']}/{md['data_session']}/assets/eiger1m-1//%Y/%m/%d/",
        read_attrs=[],
        root=f"{PROPOSALS}/{md['cycle']}/{md['data_session']}/assets/eiger1m-1/",
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
    photon_energy      = C(EpicsSignalWithRBV, 'cam1:PhotonEnergy')

    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        #self.stage_sigs.update([(self.cam.trigger_mode, "Internal Server")])

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


class BMMEigerSingleTrigger(SingleTriggerV33, BMMEiger):
    pass


