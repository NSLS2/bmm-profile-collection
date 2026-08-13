"""
Ophyd Async implementation for Eiger detector.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from logging import getLogger
from pathlib import Path
from typing import Annotated as A
from urllib.parse import urlunparse

import numpy as np
from ophyd_async.core import (
    AsyncStatus,
    DetectorAcquireLogic,
    DetectorDataLogic,
    DetectorTriggerLogic,
    PathInfo,
    PathProvider,
    SignalDatatypeT,
    SignalR,
    SignalRW,
    StandardReadable,
    StandardReadableFormat,
    StreamResourceDataProvider,
    StreamResourceInfo,
    StrictEnum,
    SubsetEnum,
    TriggerInfo,
    observe_value,
    set_and_wait_for_other_value,
)
from ophyd_async.core._status import WatchableAsyncStatus
from ophyd_async.core._utils import (
    DEFAULT_TIMEOUT,
    WatcherUpdate,
    error_if_none,
)
from ophyd_async.epics.adcore import (
    ADBaseIO,
    ADImageMode,
    ADState,
    AreaDetector,
    NDFileIO,
    NDPluginBaseIO,
    NDROIIO,
    NDROIStatIO,
    NDROIStatNIO,
    NDStatsIO,
)
from ophyd_async.epics.core import EpicsDevice, EpicsOptions, PvSuffix, stop_busy_record

async def trigger_info_from_num_images(driver: ADBaseIO) -> TriggerInfo:
    """Default TriggerInfo for AD detectors, reading num_images from the driver."""
    num = await driver.num_images.get_value()
    livetime = await driver.acquire_time.get_value()
    return TriggerInfo(collections_per_event=max(1, num), livetime=livetime)


logger = getLogger(__name__)


# TODO - add extra options in eiger2 and revert to StrictEnum
class EigerTriggerMode(SubsetEnum):
    """Trigger modes for the Eiger detector.

    See https://areadetector.github.io/areaDetector/ADEiger/eiger.html#implementation-of-standard-driver-parameters
    """

    INTERNAL_SERIES = "Internal Series"
    INTERNAL_ENABLE = "Internal Enable"
    EXTERNAL_SERIES = "External Series"
    EXTERNAL_ENABLE = "External Enable"
    CONTINUOUS = "Continuous"


class EigerExtGateMode(StrictEnum):
    """External gate modes for the Eiger detector.

    See https://areadetector.github.io/areaDetector/ADEiger/eiger.html#trigger-setup
    """

    PUMP_AND_PROBE = "Pump & Probe"
    HDR = "HDR"


class EigerROIMode(StrictEnum):
    """ROI modes for the Eiger detector.

    See https://areadetector.github.io/areaDetector/ADEiger/eiger.html#readout-setup
    """

    DISABLED = "Disable"
    _4M = "4M"


# TODO - add extra options in eiger2 and revert to StrictEnum
class EigerCompressionAlgo(SubsetEnum):
    """Compression algorithms for the Eiger detector.

    See https://areadetector.github.io/areaDetector/ADEiger/eiger.html#readout-setup
    """

    LZ4 = "LZ4"
    BSLZ4 = "BS LZ4"


class EigerDataSource(StrictEnum):
    """Data sources for the Eiger detector.

    See https://areadetector.github.io/areaDetector/ADEiger/eiger.html#readout-setup
    """

    NONE = "None"
    FILE_WRITER = "FileWriter"
    STREAM = "Stream"


class EigerHDF5Format(StrictEnum):
    """HDF5 formats for the Eiger detector.

    See https://areadetector.github.io/areaDetector/ADEiger/eiger.html#filewriter-interface
    """

    LEGACY = "Legacy"
    V2024_2 = "v2024.2"


class EigerStreamVersion(StrictEnum):
    """Stream versions for the Eiger detector.

    See https://areadetector.github.io/areaDetector/ADEiger/eiger.html#stream-interface
    """

    STREAM1 = "Stream"
    STREAM2 = "Stream2"


class EigerStreamHdrDetail(StrictEnum):
    """Header detail levels for the Eiger detector.

    See https://areadetector.github.io/areaDetector/ADEiger/eiger.html#stream-interface
    """

    ALL = "All"
    BASIC = "Basic"
    NONE = "None"


class EigerDriverIO(ADBaseIO, NDFileIO):
    """Defines the full specifics of the Eiger driver.

    See https://areadetector.github.io/areaDetector/ADEiger/eiger.html#implementation-of-standard-driver-parameters
    """

    # Standard Driver Parameters
    trigger_mode: A[SignalRW[EigerTriggerMode], PvSuffix.rbv("TriggerMode")]
    num_images: A[SignalRW[int], PvSuffix.rbv("NumImages")]
    num_images_counter: A[SignalR[int], PvSuffix("NumImagesCounter_RBV")]
    num_exposures: A[SignalRW[int], PvSuffix.rbv("NumExposures")]
    acquire_time: A[SignalRW[float], PvSuffix.rbv("AcquireTime")]
    acquire_period: A[SignalRW[float], PvSuffix.rbv("AcquirePeriod")]
    temperature_actual: A[SignalR[float], PvSuffix("TemperatureActual")]
    max_size_x: A[SignalR[int], PvSuffix("MaxSizeX_RBV")]
    max_size_y: A[SignalR[int], PvSuffix("MaxSizeY_RBV")]
    array_size_x: A[SignalR[int], PvSuffix("ArraySizeX_RBV")]
    array_size_y: A[SignalR[int], PvSuffix("ArraySizeY_RBV")]
    manufacturer: A[SignalR[str], PvSuffix("Manufacturer_RBV")]
    model: A[SignalR[str], PvSuffix("Model_RBV")]
    serial_number: A[SignalR[str], PvSuffix("SerialNumber_RBV")]
    firmware_version: A[SignalR[str], PvSuffix("FirmwareVersion_RBV")]
    sdk_version: A[SignalR[str], PvSuffix("SDKVersion_RBV")]
    driver_version: A[SignalR[str], PvSuffix("DriverVersion_RBV")]

    # Detector Information
    description: A[SignalR[str], PvSuffix("Description_RBV")]
    x_pixel_size: A[SignalR[float], PvSuffix("XPixelSize_RBV")]
    y_pixel_size: A[SignalR[float], PvSuffix("YPixelSize_RBV")]
    sensor_material: A[SignalR[str], PvSuffix("SensorMaterial_RBV")]
    sensor_thickness: A[SignalR[float], PvSuffix("SensorThickness_RBV")]
    dead_time: A[SignalR[float], PvSuffix("DeadTime_RBV")]

    # Detector Status
    state: A[SignalR[str], PvSuffix("State_RBV")]
    error: A[SignalR[str], PvSuffix("Error_RBV")]
    temp0: A[SignalR[float], PvSuffix("Temp0_RBV")]
    humid0: A[SignalR[float], PvSuffix("Humid0_RBV")]

    # Acquisition Setup
    photon_energy: A[SignalRW[float], PvSuffix.rbv("PhotonEnergy")]

    # Trigger Setup
    trigger_: A[SignalRW[float], PvSuffix("Trigger")]
    trigger_exposure: A[SignalRW[float], PvSuffix.rbv("TriggerExposure")]
    num_triggers: A[SignalRW[int], PvSuffix.rbv("NumTriggers")]
    manual_trigger: A[SignalRW[bool], PvSuffix.rbv("ManualTrigger")]

    # Readout Setup
    roi_mode: A[SignalRW[EigerROIMode], PvSuffix.rbv("ROIMode")]
    flatfield_applied: A[SignalRW[bool], PvSuffix.rbv("FlatfieldApplied")]
    countrate_corr_applied: A[SignalRW[bool], PvSuffix.rbv("CountrateCorrApplied")]
    pixel_mask_applied: A[SignalRW[bool], PvSuffix.rbv("PixelMaskApplied")]
    auto_summation: A[SignalRW[bool], PvSuffix.rbv("AutoSummation")]
    compression_algo: A[SignalRW[EigerCompressionAlgo], PvSuffix.rbv("CompressionAlgo")]
    data_source: A[SignalRW[EigerDataSource], PvSuffix.rbv("DataSource")]

    # Acquisition Status
    armed: A[SignalR[bool], PvSuffix("Armed")]
    bit_depth_image: A[SignalR[int], PvSuffix("BitDepthImage_RBV")]
    count_cutoff: A[SignalR[float], PvSuffix("CountCutoff_RBV")]

    # Stream Interface
    stream_enable: A[SignalRW[bool], PvSuffix.rbv("StreamEnable")]
    stream_state: A[SignalR[str], PvSuffix("StreamState_RBV")]
    stream_decompress: A[SignalRW[bool], PvSuffix.rbv("StreamDecompress")]
    stream_hdr_detail: A[
        SignalRW[EigerStreamHdrDetail], PvSuffix.rbv("StreamHdrDetail")
    ]
    stream_dropped: A[SignalR[int], PvSuffix("StreamDropped_RBV")]

    # Monitor Interface
    monitor_enable: A[SignalRW[bool], PvSuffix.rbv("MonitorEnable")]
    monitor_state: A[SignalR[str], PvSuffix("MonitorState_RBV")]
    monitor_timeout: A[SignalRW[float], PvSuffix.rbv("MonitorTimeout")]

    # Acquisition Metadata
    beam_x: A[SignalRW[float], PvSuffix.rbv("BeamX")]
    beam_y: A[SignalRW[float], PvSuffix.rbv("BeamY")]
    det_dist: A[SignalRW[float], PvSuffix.rbv("DetDist")]
    wavelength: A[SignalRW[float], PvSuffix.rbv("Wavelength")]

    # Detector Metadata
    chi_start: A[SignalRW[float], PvSuffix.rbv("ChiStart")]
    chi_incr: A[SignalRW[float], PvSuffix.rbv("ChiIncr")]
    kappa_start: A[SignalRW[float], PvSuffix.rbv("KappaStart")]
    kappa_incr: A[SignalRW[float], PvSuffix.rbv("KappaIncr")]
    omega_start: A[SignalRW[float], PvSuffix.rbv("OmegaStart")]
    omega_incr: A[SignalRW[float], PvSuffix.rbv("OmegaIncr")]
    phi_start: A[SignalRW[float], PvSuffix.rbv("PhiStart")]
    phi_incr: A[SignalRW[float], PvSuffix.rbv("PhiIncr")]
    two_theta_start: A[SignalRW[float], PvSuffix.rbv("TwoThetaStart")]
    two_theta_incr: A[SignalRW[float], PvSuffix.rbv("TwoThetaIncr")]

    # Minimum change allowed
    wavelength_eps: A[SignalRW[float], PvSuffix.rbv("WavelengthEps")]
    energy_eps: A[SignalRW[float], PvSuffix.rbv("EnergyEps")]

    # FileWriter Interface
    fw_enable: A[SignalRW[bool], PvSuffix.rbv("FWEnable")]
    fw_state: A[SignalR[str], PvSuffix("FWState_RBV")]
    fw_compression: A[SignalRW[bool], PvSuffix.rbv("FWCompression")]
    fw_name_pattern: A[SignalRW[str], PvSuffix.rbv("FWNamePattern")]
    sequence_id: A[SignalR[int], PvSuffix("SequenceId")]
    save_files: A[SignalRW[bool], PvSuffix.rbv("SaveFiles")]
    file_owner: A[SignalRW[str], PvSuffix.rbv("FileOwner")]
    file_owner_grp: A[SignalRW[str], PvSuffix.rbv("FileOwnerGrp")]
    file_perms: A[SignalRW[float], PvSuffix.rbv("FilePerms")]
    fw_free: A[SignalR[float], PvSuffix("FWFree_RBV")]
    fw_auto_remove: A[SignalRW[bool], PvSuffix.rbv("FWAutoRemove")]
    fw_nimgs_per_file: A[SignalRW[int], PvSuffix.rbv("FWNImagesPerFile")]


class Eiger2DriverIO(EigerDriverIO):
    """Eiger2 driver interface."""

    # Detector Status
    hv_reset_time: A[SignalRW[float], PvSuffix.rbv("HVResetTime")]
    hv_reset: A[SignalRW[bool], PvSuffix("HVReset", "HVReset")]
    hv_state: A[SignalR[str], PvSuffix("HVState_RBV")]

    # Acquisition Setup
    threshold: A[SignalRW[float], PvSuffix.rbv("ThresholdEnergy")]
    threshold1_enable: A[SignalRW[bool], PvSuffix.rbv("Threshold1Enable")]
    threshold2: A[SignalRW[float], PvSuffix.rbv("Threshold2Energy")]
    threshold2_enable: A[SignalRW[bool], PvSuffix.rbv("Threshold2Enable")]
    threshold_diff_enable: A[SignalRW[bool], PvSuffix.rbv("ThresholdDiffEnable")]
    counting_mode: A[SignalRW[str], PvSuffix.rbv("CountingMode")]

    # Trigger Setup
    ext_gate_mode: A[SignalRW[str], PvSuffix.rbv("ExtGateMode")]
    trigger_start_delay: A[SignalRW[float], PvSuffix.rbv("TriggerStartDelay")]

    # Readout Setup
    signed_data: A[SignalRW[bool], PvSuffix.rbv("SignedData")]

    # Stream Interface
    stream_version: A[SignalRW[EigerStreamVersion], PvSuffix.rbv("StreamVersion")]
    stream_hdr_appendix: None
    stream_img_appendix: None

    # FileWriter Interface
    fw_hdf5_format: A[SignalRW[EigerHDF5Format], PvSuffix.rbv("FWHDF5Format")]


class EigerController(DetectorTriggerLogic):
    """Controller for Eiger detector, handling trigger modes and acquisition setup."""

    def __init__(self, driver: EigerDriverIO) -> None:
        self.driver = driver

    def get_deadtime(self, exposure: float | None) -> float:
        """Get detector deadtime for the given exposure."""
        default_deadtime = 0.000001
        if exposure is not None:
            logger.warning(
                "Ignoring exposure to calculate deadtime: %s, defaulting to %s",
                exposure,
                default_deadtime,
            )
        return default_deadtime

    async def prepare_internal(self, num: int, livetime: float, deadtime: float):  # noqa: ARG002
        """Prepare the detector for acquisition.
        https://areadetector.github.io/areaDetector/ADEiger/eiger.html#implementation-of-standard-driver-parameters
        """
        # TODO - should we do something with deadtime?
        # TODO - put other awaits into the gather
        if livetime > 0:
            await self.driver.acquire_time.set(livetime)

        await self.driver.trigger_mode.set(EigerTriggerMode.INTERNAL_SERIES)

        if num == 0:
            image_mode = ADImageMode.CONTINUOUS
        else:
            image_mode = ADImageMode.MULTIPLE

        # TODO - should we set num_images here?
        # num_triggers gets overwritten in .prepare_unbounded(), which gets called further
        # alone in .prepare()
        # await self.driver.num_triggers.set(num),
        await asyncio.gather(
            self.driver.image_mode.set(image_mode),
        )

    # TODO should num_triggers or num_images be set?
    # TODO - put other awaits into the gather
    async def prepare_edge(self, num: int, livetime: float):
        """Prepare the detector to take external edge triggered exposures.

        :param num: the number of exposures to take
        :param livetime: how long the exposure should be, 0 means what is currently set
        """

        await self.driver.acquire_time.set(livetime)
        await self.driver.num_triggers.set(num)
        if num == 0:
            image_mode = ADImageMode.CONTINUOUS
        else:
            image_mode = ADImageMode.MULTIPLE

        await self.driver.trigger_mode.set(EigerTriggerMode.EXTERNAL_SERIES)
        await asyncio.gather(
            self.driver.image_mode.set(image_mode),
        )

    async def default_trigger_info(self):
        return await trigger_info_from_num_images(self.driver)


class EigerDataLogic(DetectorDataLogic):
    """Eiger-specific file writer using the built-in FileWriter interface."""

    default_suffix: str = "cam1:"
    # Forced minimum number of images per file to force a single HDF5 file
    _min_num_images_per_file: int = 1_000_000_000
    datakey_suffix: str = "_image"

    def __init__(
        self,
        fileio: EigerDriverIO,
        path_provider: PathProvider,
    ):
        self.fileio = fileio
        self._path_provider = path_provider

        self._file_info: PathInfo | None = None
        self._datasets: list[StreamResourceDataProvider] = []
        self._master_file_path_cache: list[Path] = []

    async def prepare_unbounded(self, datakey_name: str) -> StreamResourceDataProvider:
        """Provider can work for an unbounded number of collections."""
        # Get file path info from path provider
        self._file_info = self._path_provider(self.fileio.parent.name)
        self._master_file_path_cache.clear()

        # Set the name pattern with $id replacement similar to original
        name_pattern = f"{self._file_info.filename}_$id"

        # Configure the Eiger FileWriter
        await asyncio.gather(
            self.fileio.file_path.set(self._file_info.directory_path.as_posix()),
            self.fileio.create_directory.set(self._file_info.create_dir_depth),
            self.fileio.fw_name_pattern.set(name_pattern),
            self.fileio.fw_enable.set(True),
            self.fileio.save_files.set(True),
            self.fileio.num_capture.set(0),
            # Use array_counter to track the total number of images written
            self.fileio.array_counter.set(0),
            self.fileio.manual_trigger.set(True),
            # TODO sort out how to get this from the plan
            self.fileio.num_triggers.set(5000),
            self.fileio.data_source.set(EigerDataSource.STREAM),
        )

        await set_and_wait_for_other_value(
            set_signal=self.fileio.acquire,
            set_value=True,
            match_signal=self.fileio.armed,
            match_value=True,
            wait_for_set_completion=False,
            timeout=DEFAULT_TIMEOUT,
        )

        if not await self.fileio.file_path_exists.get_value():
            msg = f"File path {self._file_info.directory_path} does not exist"
            raise FileNotFoundError(msg)

        if isinstance(self.fileio, Eiger2DriverIO):
            await self.fileio.fw_hdf5_format.set(EigerHDF5Format.LEGACY)

        # Force the number of images per file to a large number to simplify the logic
        # TODO: allow multiple files
        num_images_per_file = await self.fileio.fw_nimgs_per_file.get_value()
        if num_images_per_file < self._min_num_images_per_file:
            await self.fileio.fw_nimgs_per_file.set(self._min_num_images_per_file)
            logger.warning(
                "Setting fw_nimgs_per_file to %d to force writing to a single HDF5 file",
                self._min_num_images_per_file,
            )
        driver = self.fileio

        shape = await asyncio.gather(
            *[sig.get_value() for sig in [driver.array_size_y, driver.array_size_x]]
        )
        datatype = "uint32"
        # Remove entries in shape that are zero
        shape = [x for x in shape if x > 0]

        mfp = await self._master_file_path

        # TODO sort out how to tell tiled about the additional data files.
        return StreamResourceDataProvider(
            uri=urlunparse(("file", "localhost", str(mfp), "", "", None)),
            resources=[
                StreamResourceInfo(
                    data_key=datakey_name,
                    shape=shape,
                    # TODO sort out how to set this and mirror here
                    chunk_shape=(1, *shape),
                    dtype_numpy=np.dtype(datatype.lower()).str,
                    parameters={
                        "dataset": f"entry/data/data_{1:06d}",
                    },
                    source="eiger",
                )
            ],
            mimetype="application/x-hdf5",
            collections_written_signal=self.fileio.array_counter,
        )

    @property
    async def _master_file_path(self) -> Path | None:
        if self._file_info is None:
            logger.warning(
                "No master file path found for file info %s",
                self._file_info,
            )
            return None
        sequence_id = await self.fileio.sequence_id.get_value()
        return Path(
            self._file_info.directory_path
            / f"{self._file_info.filename}_{sequence_id}_master.h5"
        )

    async def observe_indices_written(
        self, timeout: float
    ) -> AsyncGenerator[int, None]:
        async for num_captured in observe_value(self.fileio.array_counter, timeout):
            yield num_captured

    async def get_indices_written(self) -> int:
        return await self.fileio.array_counter.get_value()

    async def stop(self) -> None:
        """Clean up file writing after acquisition and validate files exist."""

        # Check that the master files were written
        # for master_file_path in self._master_file_path_cache:
        #     if not master_file_path.exists():
        #         ...

        self._file_info = None
        await self.fileio.fw_enable.set(False)


# TODO sort out if ths is the right name of things
class EigerAcquireLogic(DetectorAcquireLogic):
    def __init__(
        self, driver: Eiger2DriverIO, driver_armed_signal: SignalR[bool] | None = None
    ):
        self._cached_trigger_mode: EigerTriggerMode | None = None
        self.driver = driver
        # TODO - remove? driver_armed_signal doesn't seem to be a thing anywhere else
        if driver_armed_signal is not None:
            self.driver_armed_signal = driver_armed_signal
        else:
            self.driver_armed_signal = driver.acquire
        self.acquire_status: AsyncStatus | None = None
        self._rolling_image_counter = 0

    async def start_acquiring(self):
        self._rolling_image_counter = await self.driver.num_images_counter.get_value()
        ret = await self.driver.trigger_.set(1)
        return ret

    async def wait_for_idle(self):
        target_num_images, frame_acquire_period = await asyncio.gather(
            self.driver.num_images.get_value(), self.driver.acquire_period.get_value()
        )
        frame_timeout = frame_acquire_period + DEFAULT_TIMEOUT
        done_timeout = frame_timeout * target_num_images
        target_num_images += self._rolling_image_counter
        async for images_complete in observe_value(
            self.driver.num_images_counter,
            timeout=frame_timeout,
            done_timeout=done_timeout,
        ):
            if images_complete == target_num_images:
                break

    # We are intentionally not calling the base class implementation
    async def ensure_ready(self):
        detector_state = await self.driver.detector_state.get_value()
        self._cached_acquire_state = detector_state == ADState.ACQUIRE
        self._cached_trigger_mode = await self.driver.trigger_mode.get_value()
        self._cached_image_mode = await self.driver.image_mode.get_value()

        self._rolling_image_counter = 0
        await stop_busy_record(self.driver.acquire)

    async def ensure_stopped(self):
        await stop_busy_record(self.driver.acquire)

        coros = []
        if self._cached_trigger_mode is not None:
            coros.append(self.driver.trigger_mode.set(self._cached_trigger_mode))
        if self._cached_image_mode is not None:
            coros.append(self.driver.image_mode.set(self._cached_image_mode))
        await asyncio.gather(
            self.driver.manual_trigger.set(False),
            self.driver.num_triggers.set(1),
            *coros,
        )
        self._cached_trigger_mode = None
        self._cached_image_mode = None

        if self._cached_acquire_state is not None:
            await self.driver.acquire.set(self._cached_acquire_state)
        self._cached_acquire_state = None


class EigerDetector(AreaDetector[Eiger2DriverIO]):
    """Eiger detector implementation using the AreaDetector pattern."""

    def __init__(
        self,
        prefix: str,
        path_provider: PathProvider,
        driver_suffix: str = "cam1:",
        name: str = "",
        config_sigs: Sequence[SignalR[SignalDatatypeT]] = (),
        plugins: dict[str, NDPluginBaseIO] | None = None,
    ):
        driver = Eiger2DriverIO(prefix + driver_suffix)
        controller = EigerController(driver)
        acquire_logic = EigerAcquireLogic(driver)
        super().__init__(
            prefix=prefix,
            driver=driver,
            trigger_logic=controller,
            name=name,
            config_sigs=config_sigs,
            plugins=plugins,
            acquire_logic=acquire_logic,
        )
        self.data_logic = EigerDataLogic(fileio=driver, path_provider=path_provider)
        self.add_detector_logics(self.data_logic)

    # TODO remove this as it should be identical to upstream.
    @WatchableAsyncStatus.wrap
    async def trigger(self) -> AsyncIterator[WatcherUpdate[int]]:
        """Trigger a single exposure.

        If [`prepare()`](#StandardDetector.prepare) has not been called since
        the last [`stage()`](#StandardDetector.stage), an implicit prepare is
        performed. When [](#OPHYD_ASYNC_PRESERVE_DETECTOR_STATE) is `YES`
        [](#DetectorTriggerLogic.default_trigger_info) is called to read current
        hardware state; otherwise a bare [`TriggerInfo()`](#TriggerInfo) is
        used.
        """
        if self._prepare_ctx is None:
            # We always want to preserve state on the eiger
            preserve_state = True
            if preserve_state and self._trigger_logic is not None:

                def _logic_supported(base_class, method) -> bool:
                    # If the function that is bound in a subclass is the same as the function
                    # attached to the superclass, then the subclass has not overridden it, so
                    # this method is not supported by the subclass.
                    return method.__func__ is not getattr(base_class, method.__name__)

                _trigger_logic_supported = functools.partial(
                    _logic_supported, DetectorTriggerLogic
                )
                if not _trigger_logic_supported(
                    self._trigger_logic.default_trigger_info
                ):
                    raise RuntimeError(
                        f"OPHYD_ASYNC_PRESERVE_DETECTOR_STATE=YES is set but "
                        f"'{self.name}' has no default_trigger_info() - implement "
                        "default_trigger_info() on your DetectorTriggerLogic subclass "
                        "or unset the environment variable."
                    )
                trigger_info = await self._trigger_logic.default_trigger_info()
            else:
                trigger_info = TriggerInfo()
            await self.prepare(trigger_info)
        else:
            # Check the one that was provided is suitable for triggering
            trigger_info = self._prepare_ctx.trigger_info
            if trigger_info.number_of_events != 1:
                msg = (
                    "trigger() is not supported for multiple events, the detector was "
                    f"prepared with number_of_events={trigger_info.number_of_events}."
                )
                raise ValueError(msg)
            # Ensure the data provider is still usable
            await self._update_prepare_context(trigger_info)
        ctx = error_if_none(self._prepare_ctx, "Prepare should have been run")
        # Arm the detector and wait for it to finish.
        if self._acquire_logic:
            await self._acquire_logic.start_acquiring()

        async for update in self._wait_for_index(
            data_providers=ctx.streamable_data_providers,
            trigger_info=ctx.trigger_info,
            initial_collections_written=ctx.collections_written,
            collections_requested=1,
            wait_for_idle=True,
        ):
            yield update    














class NDROIIORW(NDPluginBaseIO):
    """Plugin for taking a region of an NDArray.

    This mirrors the interface provided by ADCore/db/NDROI.template.
    See HTML docs at https://areadetector.github.io/areaDetector/ADCore/NDPluginROI.html
    """

    name_:  A[SignalRW[str],  PvSuffix.rbv("Name")]
    size_x: A[SignalRW[int],  PvSuffix.rbv("SizeX")]
    size_y: A[SignalRW[int],  PvSuffix.rbv("SizeY")]
    size_z: A[SignalRW[int],  PvSuffix.rbv("SizeZ")]
    min_x:  A[SignalRW[int],  PvSuffix.rbv("MinX")]
    min_y:  A[SignalRW[int],  PvSuffix.rbv("MinY")]
    min_z:  A[SignalRW[int],  PvSuffix.rbv("MinZ")]




            
class NDCentroid(StandardReadable, EpicsDevice):
    X: A[
        SignalR[float], PvSuffix("CentroidX_RBV"), StandardReadableFormat.HINTED_SIGNAL
    ]
    Y: A[
        SignalR[float], PvSuffix("CentroidY_RBV"), StandardReadableFormat.HINTED_SIGNAL
    ]
    SigmaX: A[SignalR[float], PvSuffix("SigmaX_RBV"), StandardReadableFormat.CHILD]
    SigmaY: A[SignalR[float], PvSuffix("SigmaY_RBV"), StandardReadableFormat.CHILD]

    SkewX: A[SignalR[float], PvSuffix("SkewX_RBV"), StandardReadableFormat.CHILD]
    SkewY: A[SignalR[float], PvSuffix("SkewY_RBV"), StandardReadableFormat.CHILD]

    KurtosisX: A[
        SignalR[float], PvSuffix("KurtosisX_RBV"), StandardReadableFormat.CHILD
    ]
    KurtosisY: A[
        SignalR[float], PvSuffix("KurtosisY_RBV"), StandardReadableFormat.CHILD
    ]

    SigmaXY: A[SignalR[float], PvSuffix("SigmaXY_RBV"), StandardReadableFormat.CHILD]
    Eccentricity: A[
        SignalR[float], PvSuffix("Eccentricity_RBV"), StandardReadableFormat.CHILD
    ]
    Orientation: A[
        SignalR[float], PvSuffix("Orientation_RBV"), StandardReadableFormat.CHILD
    ]

class NDSTatistics(StandardReadable, EpicsDevice):
    MinValue  : A[SignalR[float], PvSuffix("MinValue_RBV"),  StandardReadableFormat.CHILD]
    MaxValue  : A[SignalR[float], PvSuffix("MaxValue_RBV"),  StandardReadableFormat.CHILD]
    MinX      : A[SignalR[float], PvSuffix("MinX_RBV"),      StandardReadableFormat.CHILD]
    MaxX      : A[SignalR[float], PvSuffix("MaxX_RBV"),      StandardReadableFormat.CHILD]
    MinY      : A[SignalR[float], PvSuffix("MinY_RBV"),      StandardReadableFormat.CHILD]
    MaxY      : A[SignalR[float], PvSuffix("MaxY_RBV"),      StandardReadableFormat.CHILD]
    Total     : A[SignalR[float], PvSuffix("Total_RBV"),     StandardReadableFormat.CHILD]
    Net       : A[SignalR[float], PvSuffix("Net_RBV"),       StandardReadableFormat.CHILD]
    MeanValue : A[SignalR[float], PvSuffix("MeanValue_RBV"), StandardReadableFormat.CHILD]
    Sigma     : A[SignalR[float], PvSuffix("Sigma_RBV"),     StandardReadableFormat.CHILD]
    
    
class EnrichedStats(NDStatsIO, StandardReadable):
    centroid: A[NDCentroid, PvSuffix(""), StandardReadableFormat.CHILD] 
    statistics: A[NDSTatistics, PvSuffix(""), StandardReadableFormat.CHILD] 

class BMMEiger(EigerDetector):
    
    stats1: EnrichedStats
    stats2: EnrichedStats
    stats3: EnrichedStats
    stats4: EnrichedStats
    
    def __init__(
        self,
        prefix: str,
        path_provider: PathProvider,
        driver_suffix: str = "cam1:",
        name: str = "",
        config_sigs: Sequence[SignalR[SignalDatatypeT]] = (),
        plugins: dict[str, NDPluginBaseIO] | None = None,        
    ):
        plugins = plugins or {}
        super().__init__(
            prefix=prefix,
            path_provider=path_provider,
            driver_suffix=driver_suffix,
            name=name,
            config_sigs=config_sigs,
            plugins={
                "stats1":  EnrichedStats(f"{prefix}Stats1:"),
                "stats2":  EnrichedStats(f"{prefix}Stats2:"),
                "stats3":  EnrichedStats(f"{prefix}Stats3:"),
                "stats4":  EnrichedStats(f"{prefix}Stats4:"),
                "roi1":    NDROIIORW(f"{prefix}ROI1:"),
                "roi2":    NDROIIORW(f"{prefix}ROI2:"),
                "roi3":    NDROIIORW(f"{prefix}ROI3:"),
                "roi4":    NDROIIORW(f"{prefix}ROI4:"),
                "roistat": NDROIStatIO(f"{prefix}ROIStat1:"),
                **plugins,
            },
        )

    async def sync_screen_rois(self, verbose=True):
        '''Synchronize the min and size values of the ROIs between the ROI
        plugin (as displayed on screen) and the ROIStats plugin.

        '''
        for i in range(1,5):
            thisroi = getattr(self, f'roi{i}')
            mx = await thisroi.min_x.get_value()
            sx = await thisroi.size_x.get_value()
            my = await thisroi.min_y.get_value()
            sy = await thisroi.size_y.get_value()

            thisroistat = self.roistat.channels.get(i)
            await thisroistat.min_x.set(mx)
            await thisroistat.size_x.set(sx)
            await thisroistat.min_y.set(my)
            await thisroistat.size_y.set(sy)

        if verbose is True:
            print('Cut-n-paste these ROI values into your spreadsheet.\n')
            print(f'ROI2: {await self.roi2.min_x.get_value()} {await self.roi2.size_x.get_value()} {await self.roi2.min_y.get_value()} {await self.roi2.size_y.get_value()}')
            print(f'ROI3: {await self.roi3.min_x.get_value()} {await self.roi3.size_x.get_value()} {await self.roi3.min_y.get_value()} {await self.roi3.size_y.get_value()}')

    async def set_rois(self, which, values):
        '''Set ROI boundaries to the supplied coordinates.  These values are
        pushed to the ROI and ROIStats plugins.

        arguments
        =========
        which : (int) ROI number, valid values: 1, 2, 3, 4
        values : (list of int) ROI min_x, size_x, min_y, size_y

        '''
        if which in (1,2,3,4):
            mx, sx, my, sy = values # .split()
            thisroi = getattr(self, f'roi{which}')
            await thisroi.min_x.set(int(mx))
            await thisroi.size_x.set(int(sx))
            await thisroi.min_y.set(int(my))
            await thisroi.size_y.set(int(sy))
            await self.sync_screen_rois(verbose=False)
        else:
            print(f"{which} is not a valid ROI number -- should be 1, 2, 3, or 4")
            
