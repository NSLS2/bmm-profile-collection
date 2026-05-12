
from pathlib import PurePath
from collections import deque, OrderedDict
import time as ttime
from tqdm import tqdm

from ophyd import Component as Cpt
from ophyd import EpicsSignal, EpicsSignalRO, EpicsSignalWithRBV as SignalWithRBV
from ophyd.areadetector import ADBase, AreaDetector, ImagePlugin, DetectorBase
from ophyd.areadetector.cam import AreaDetectorCam
from ophyd.areadetector.base import EpicsSignalWithRBV, ADComponent as ADCpt
from ophyd.areadetector.filestore_mixins import resource_factory, FileStoreHDF5, FileStoreTIFF, FileStoreIterativeWrite, FileStorePluginBase
from ophyd.areadetector.plugins import HDF5Plugin_V33, TIFFPlugin_V33, StatsPlugin_V33

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)
md = user_ns["RE"].md

from nslsii.ad33 import SingleTriggerV33
from ophyd import Component as C


from bmm_tools.tools.messages import error_msg, warning_msg, go_msg, url_msg, bold_msg, verbosebold_msg, list_msg, disconnected_msg, info_msg, whisper

import matplotlib.pyplot as plt
import numpy, xraylib
from bmm_tools.tools.periodictable import Z_number, edge_number

from BMM.user_ns.base import PROPOSALS

###########################################################################
# ______  ___   _   _ _____ _____              ___   _____________ _____  #
# |  _  \/ _ \ | \ | |_   _|  ___|            / / | | |  _  \  ___|  ___| #
# | | | / /_\ \|  \| | | | | |__   __      __/ /| |_| | | | | |_  |___ \  #
# | | | |  _  || . ` | | | |  __|  \ \ /\ / / / |  _  | | | |  _|     \ \ #
# | |/ /| | | || |\  | | | | |___   \ V  V / /  | | | | |/ /| |   /\__/ / #
# |___/ \_| |_/\_| \_/ \_/ \____/    \_/\_/_/   \_| |_/___/ \_|   \____/  #
###########################################################################


#from bmm_tools.devices.pilatus import BMMFileStoreHDF5, BMMHDF5Plugin

class BMMDanteFileStoreHDF5(FileStorePluginBase):
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
            "frame_per_point": self.get_frames_per_point(),
        }
        self._generate_resource(resource_kwargs)

class BMMDanteHDF5Plugin(HDF5Plugin_V33, BMMDanteFileStoreHDF5, FileStoreIterativeWrite):


    def _update_paths(self):
        self.reg_root = self.root_path_str
        self._write_path_template = self.root_path_str  + self.path_template_str
        self._read_path_template = self.root_path_str + self.path_template_str

    @property
    def root_path_str(self):
        root_path = f"{PROPOSALS}/{md['cycle']}/{md['data_session']}/assets/dante-1/"
        return root_path

    @property
    def path_template_str(self):
        path_template = "%Y/%m/%d"
        return path_template

    def stage(self, *args, **kwargs):
        self._update_paths()
        super().stage(*args, **kwargs)


    def warmup(self):
        """
        A convenience method for "priming" the plugin.
        The plugin has to "see" one acquisition before it is ready to capture.
        This sets the array size, etc.
        NOTE : this comes from:
            https://github.com/NSLS-II/ophyd/blob/master/ophyd/areadetector/plugins.py
        We had to replace "cam" with "settings" here.

        This has been slightly modified by Bruce to avoid a situation where the warmup
        hangs.  Also to add some indication on screen for what is happening.
        """
        print("                        warming up the Dante hdf5 plugin...", flush=True)
        self.enable.set(1).wait()

        # JOSH: proposed changes for new IOC
        sigs = OrderedDict([(self.parent.cam.array_callbacks, 1),
                            #(self.parent.cam.image_mode, "Single"),
                            #(self.parent.cam.trigger_mode, 'Internal'),
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


from ophyd.utils import enum
from ophyd.device import DynamicDeviceComponent as DDC
from ophyd.areadetector.base import ad_group

class DanteCamBase(ADBase):
    _default_configuration_attrs = ADBase._default_configuration_attrs + (
        "acquire_time",
        #"acquire_period",
        "model",
        #"num_exposures",
        #"image_mode",
        "num_images",
        "manufacturer",
        #"trigger_mode",
    )
    collect_mode = ADCpt(SignalWithRBV, "CollectMode")

    #ImageMode = enum(SINGLE=0, MULTIPLE=1, CONTINUOUS=2)

    # Shared among all cams and plugins
    array_counter = ADCpt(SignalWithRBV, "ArrayCounter")
    array_rate = ADCpt(EpicsSignalRO, "ArrayRate_RBV")
    asyn_io = ADCpt(EpicsSignal, "AsynIO")

    num_mca_channels = ADCpt(SignalWithRBV, "NumMCAChannels")

    nd_attributes_file = ADCpt(EpicsSignal, "NDAttributesFile", string=True)
    pool_alloc_buffers = ADCpt(EpicsSignalRO, "PoolAllocBuffers")
    pool_free_buffers = ADCpt(EpicsSignalRO, "PoolFreeBuffers")
    pool_max_buffers = ADCpt(EpicsSignalRO, "PoolMaxBuffers")
    pool_max_mem = ADCpt(EpicsSignalRO, "PoolMaxMem")
    pool_used_buffers = ADCpt(EpicsSignalRO, "PoolUsedBuffers")
    pool_used_mem = ADCpt(EpicsSignalRO, "PoolUsedMem")
    port_name = ADCpt(EpicsSignalRO, "PortName_RBV", string=True)

    # Cam-specific
    acquire = ADCpt(EpicsSignal, "EraseStart")
    #acquire_period = ADCpt(SignalWithRBV, "PollTime")
    #acquire_time = ADCpt(SignalWithRBV, "PollTime")
    #acquire_period = ADCpt(SignalWithRBV, "PollTime")
    acquire_time = ADCpt(EpicsSignal, "PresetReal")

    array_callbacks = ADCpt(SignalWithRBV, "ArrayCallbacks")
    array_size = DDC(
        ad_group(
            EpicsSignalRO,
            (
                ("array_size_z", "ArraySizeZ_RBV"),
                ("array_size_y", "ArraySizeY_RBV"),
                ("array_size_x", "ArraySizeX_RBV"),
            ),
        ),
        doc="Size of the array in the XYZ dimensions",
    )
    array_size_bytes = ADCpt(EpicsSignalRO, "ArraySize_RBV")
    bin_x = ADCpt(SignalWithRBV, "BinX")
    bin_y = ADCpt(SignalWithRBV, "BinY")
    color_mode = ADCpt(SignalWithRBV, "ColorMode")
    data_type = ADCpt(SignalWithRBV, "DataType")
    detector_state = ADCpt(EpicsSignalRO, "DetectorState_RBV")
    frame_type = ADCpt(SignalWithRBV, "FrameType")
    gain = ADCpt(SignalWithRBV, "Gain")

    #image_mode = ADCpt(SignalWithRBV, "ImageMode")
    manufacturer = ADCpt(EpicsSignalRO, "Manufacturer_RBV")

    max_size = DDC(
        ad_group(
            EpicsSignalRO,
            (("max_size_x", "MaxSizeX_RBV"), ("max_size_y", "MaxSizeY_RBV")),
        ),
        doc="Maximum sensor size in the XY directions",
    )

    min_x = ADCpt(SignalWithRBV, "MinX")
    min_y = ADCpt(SignalWithRBV, "MinY")
    model = ADCpt(EpicsSignalRO, "Model_RBV")


    num_exposures = ADCpt(SignalWithRBV, "NumExposures")
    num_exposures_counter = ADCpt(EpicsSignalRO, "NumExposuresCounter_RBV")
    num_images = ADCpt(SignalWithRBV, "MappingPoints")   # , "NumImages")
    #num_images_counter = ADCpt(EpicsSignalRO, "NumImagesCounter_RBV")

    read_status = ADCpt(EpicsSignal, "ReadStatus")
    reverse = DDC(
        ad_group(SignalWithRBV, (("reverse_x", "ReverseX"), ("reverse_y", "ReverseY")))
    )

    shutter_close_delay = ADCpt(SignalWithRBV, "ShutterCloseDelay")
    shutter_close_epics = ADCpt(EpicsSignal, "ShutterCloseEPICS")
    shutter_control = ADCpt(SignalWithRBV, "ShutterControl")
    shutter_control_epics = ADCpt(EpicsSignal, "ShutterControlEPICS")
    shutter_fanout = ADCpt(EpicsSignal, "ShutterFanout")
    shutter_mode = ADCpt(SignalWithRBV, "ShutterMode")
    shutter_open_delay = ADCpt(SignalWithRBV, "ShutterOpenDelay")
    shutter_open_epics = ADCpt(EpicsSignal, "ShutterOpenEPICS")
    shutter_status_epics = ADCpt(EpicsSignalRO, "ShutterStatusEPICS_RBV")
    shutter_status = ADCpt(EpicsSignalRO, "ShutterStatus_RBV")

    size = DDC(ad_group(SignalWithRBV, (("size_x", "SizeX"), ("size_y", "SizeY"))))

    status_message = ADCpt(EpicsSignalRO, "StatusMessage_RBV", string=True)
    string_from_server = ADCpt(EpicsSignalRO, "StringFromServer_RBV", string=True)
    string_to_server = ADCpt(EpicsSignalRO, "StringToServer_RBV", string=True)
    temperature = ADCpt(SignalWithRBV, "Temperature")
    temperature_actual = ADCpt(EpicsSignal, "TemperatureActual")
    time_remaining = ADCpt(EpicsSignalRO, "TimeRemaining_RBV")
    #trigger_mode = ADCpt(SignalWithRBV, "TriggerMode")

    keep_alive = ADCpt(EpicsSignal, "KeepAlive")


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)



class DanteChannel():
    def __init__(self, prefix, channel, *args, **kwargs):
        EpicsSignal(f'{prefix}ROIStat1:{channel}:Use', name='').put(1)
        self.energy_start  = EpicsSignal(f'{prefix}ROIStat1:{channel}:MinX',  name=f'roi{channel}_minx')
        self.energy_width  = EpicsSignal(f'{prefix}ROIStat1:{channel}:SizeX', name=f'roi{channel}_sizex')
        self.channel_start = EpicsSignal(f'{prefix}ROIStat1:{channel}:MinY',  name=f'roi{channel}_miny')
        self.channel_width = EpicsSignal(f'{prefix}ROIStat1:{channel}:SizeY', name=f'roi{channel}_sizey')
        self.channel_start.put(channel-1)
        self.channel_width.put(1)

    def set_roi(self, start, width):
        self.energy_start.put(start)
        self.energy_width.put(width)


class BMMDante(DetectorBase):
    #image = Cpt(ImagePlugin, "image1:")
    cam = Cpt(DanteCamBase, "dante:")
    #acquire_period = ADCpt(SignalWithRBV, "PollTime")
    acquire_time = ADCpt(EpicsSignal, "dante:PresetReal")
    acquire = ADCpt(EpicsSignal, "dante:EraseStart")


    mca1 = ADCpt(EpicsSignal, "mca1")
    mca2 = ADCpt(EpicsSignal, "mca2")
    mca3 = ADCpt(EpicsSignal, "mca3")
    mca4 = ADCpt(EpicsSignal, "mca4")
    mca5 = ADCpt(EpicsSignal, "mca5")
    mca6 = ADCpt(EpicsSignal, "mca6")
    mca7 = ADCpt(EpicsSignal, "mca7")
    #mca8 = ADCpt(EpicsSignal, "mca8")

    hdf5 = Cpt(
        BMMDanteHDF5Plugin,
        "HDF1:",
        write_path_template=f"{PROPOSALS}/{md['cycle']}/{md['data_session']}/assets/dante-1/%Y/%m/%d/",
        read_path_template=f"{PROPOSALS}/{md['cycle']}/{md['data_session']}/assets/dante-1//%Y/%m/%d/",
        read_attrs=[],
        root=f"{PROPOSALS}/{md['cycle']}/{md['data_session']}/assets/dante-1/",
    )
    roi1 = Cpt(EpicsSignalRO, "ROIStat1:1:Total_RBV")
    roi2 = Cpt(EpicsSignalRO, "ROIStat1:2:Total_RBV")
    roi3 = Cpt(EpicsSignalRO, "ROIStat1:3:Total_RBV")
    roi4 = Cpt(EpicsSignalRO, "ROIStat1:4:Total_RBV")
    roi5 = Cpt(EpicsSignalRO, "ROIStat1:5:Total_RBV")
    roi6 = Cpt(EpicsSignalRO, "ROIStat1:6:Total_RBV")
    roi7 = Cpt(EpicsSignalRO, "ROIStat1:7:Total_RBV")
    #roi8 = Cpt(EpicsSignalRO, "ROIStat1:8:Total_RBV")

    nchannels = 8

    def make_data_key(self):
        source = "PV:{}".format(self.prefix)
        # This shape is expected to match arr.shape for the array.
        shape = (
            1, #self.cam.num_images,
            8,
            1024 * pow(2, self.cam.num_mca_channels.get()),
        )

        data_key = dict(
            shape=shape,
            source=source,
            dtype="array",
            dtype_str="|u2",
            external="FILESTORE:",
        )
        #print(data_key)
        return data_key


    def measure_xrf(self, exposure=1.0, doplot=True):
        '''Measure, table, plot -- in a package suitable for an ipython magic.
        '''
        uid = None
        #self.total_points.put(1)
        self.acquire_time.put(exposure)
        self.acquire.put(1)
        ttime.sleep(exposure + 0.5)
        #self.table()
        if doplot:
            self.plot(add=True, uid=uid)


    def plot(self, uid=None, add=False, only=None):
        '''Make a plot appropriate for the N-element detector.

        The default is to sum the four channels.

        Parameters
        ----------
        uid : str
            DataBroker UID. If None, use the current values in the IOC
        add : bool
            If True, plot the sum of the four channels
        only : int
            plot only the signal from a specific channel -- (1) / (1-4) / (1-7)

        '''
        if uid is not None:
            kafka.message({'xrf': 'plot', 'uid': uid, 'add': add, 'only': only})
        else:
            dcm, BMMuser = user_ns['dcm'], user_ns['BMMuser']
            plt.clf()
            plt.xlabel('Energy  (eV)')
            plt.ylabel('counts')
            plt.grid(which='major', axis='both')
            #plt.xlim(2500, round(dcm.energy.position, -2)+500)
            plt.xlim(0, 20480)
            plt.title(f'XRF Spectrum {BMMuser.element} {BMMuser.edge} (Dante)')
            # s = list()
            # for channel in self.iterate_channels():
            #     s.append(channel.mca.array_data.get())
            e = numpy.arange(0, len(self.mca1.get())) * 10
            plt.ion()
            plt.plot(e, self.mca1.get(), label=f'channel 1')

            # if only is not None and only in range(1, len(list(self.iterate_channels()))+1):
            #     channel = self.get_channel(channel_number=only)
            #     this = channel.mca.array_data
            #     plt.plot(e, this.get(), label=f'channel {only}')
            # elif add is True:
            #     plt.plot(e, sum(s), label=f'sum of {len(list(self.iterate_channels()))} channels')
            # else:
            #     for i, sig in enumerate(s):
            #         plt.plot(e, sig, label=f'channel {i}')
            z = Z_number(BMMuser.element)
            if BMMuser.edge.lower() == 'k':
                label = f'{BMMuser.element} Kα1'
                ke = (2*xraylib.LineEnergy(z, xraylib.KL3_LINE) + xraylib.LineEnergy(z, xraylib.KL2_LINE))*1000/3
                plt.axvline(x = ke/1.0016,  color = 'brown', linewidth=1, label=label)
            elif BMMuser.edge.lower() == 'l3':
                label = f'{BMMuser.element} Lα1'
                plt.axvline(x = xraylib.LineEnergy(z, xraylib.L3M5_LINE)*1000, color = 'brown', linewidth=1, label=label)
            elif BMMuser.edge.lower() == 'l2':
                label = f'{BMMuser.element} Kβ1'
                plt.axvline(x = xraylib.LineEnergy(z, xraylib.L2M4_LINE)*1000, color = 'brown', linewidth=1, label=label)
            elif BMMuser.edge.lower() == 'l1':
                label = f'{BMMuser.element} Kβ3'
                plt.axvline(x = xraylib.LineEnergy(z, xraylib.L1M3_LINE)*1000, color = 'brown', linewidth=1, label=label)
            plt.legend()
            #plt.show()



from BMM.user_ns.base import profile_configuration
import redis


class BMMDanteSingleTrigger(SingleTriggerV33, BMMDante):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        del self.stage_sigs["cam.image_mode"]
        del self.stage_sigs["cam.acquire"]
        self.stage_sigs["cam.collect_mode"] = 1

        self.channel1 = DanteChannel(self.prefix, 1)
        self.channel2 = DanteChannel(self.prefix, 2)
        self.channel3 = DanteChannel(self.prefix, 3)
        self.channel4 = DanteChannel(self.prefix, 4)
        self.channel5 = DanteChannel(self.prefix, 5)
        self.channel6 = DanteChannel(self.prefix, 6)
        self.channel7 = DanteChannel(self.prefix, 7)
        #self.channel8 = DanteChannel(self.prefix, 8)  # Abdul says it's a 7-element detector

    def set_rois(self, start, size):
        bmm_redis = profile_configuration.get('services', 'bmm_redis')
        rkvs = redis.Redis(host=bmm_redis, port=6379, db=0)
        for i in range(1,8):
            thischan = getattr(self, f'channel{i}')
            thischan.set_roi(start, size)
            thisroi = getattr(self, f'roi{i}')
            name = user_ns['BMMuser'].element + str(i)
            thisroi.name = name
            #getattr(user_ns['BMMuser'], f'xs{i}') = name
            rkvs.set(f'BMM:user:xs{i}', name)
            EpicsSignal(f'{self.prefix}ROIStat1:{i}:Name',  name=f'').put(name)
