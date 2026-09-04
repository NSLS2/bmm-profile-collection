import numpy
import os

from tiled.client import from_uri
from bluesky.callbacks.buffer import BufferingWrapper
from bluesky_tiled_plugins import TiledWriter
from bluesky_tiled_plugins.writing.consolidators import MultipartRelatedConsolidator

from BMM.user_ns.base import RE

# Define a mapping from spec to mimetype
# TODO: Only keep necessary specs/mimetypes
MIMETYPE_LOOKUP = {
    "hdf5": "application/x-hdf5",
    "AD_CBF": "multipart/related;type=image/tiff",
    "AD_JPEG": "multipart/related;type=image/jpeg",
    "AD_HDF5": "application/x-hdf5",
    "AD_HDF5_GERM": "application/x-hdf5",
    "AD_HDF5_SWMR_STREAM": "application/x-hdf5",
    "AD_HDF5_SWMR_SLICE": "application/x-hdf5",
    "AD_HDF5_SWMR": "application/x-hdf5",
    "AD_TIFF": "multipart/related;type=image/tiff",
    "AD_TIFF_TS": "application/x-metadata;source=tiff",  # 
    "BEAMLINE_WEBCAM": "multipart/related;type=image/jpeg",  # BMM
    "BMM_USBCAM": "multipart/related;type=image/jpeg",  # bmm_patches:BMM_JPEG_HANDLER
    "BMM_XAS_WEBCAM": "multipart/related;type=image/jpeg",  # bmm_patches:BMM_JPEG_HANDLER
    "BMM_XRD_WEBCAM": "multipart/related;type=image/jpeg",  # bmm_patches:BMM_JPEG_HANDLER
    "BMM_ANALOG_CAMERA": "multipart/related;type=image/jpeg",  # bmm_patches:BMM_JPEG_HANDLER
    "BMM_ANALOG_CAMERA_SINGLE": "image/jpeg",
    "BMM_JPEG_HANDLER": "multipart/related;type=image/jpeg",
    "DEX_HDF5": "application/x-hdf5",
    "DEXELA_FLY_V1": "application/x-hdf5",
    "EIGER2_STREAM": "application/x-hdf5",
    "MERLIN_FLY_STREAM_V1": "application/x-hdf5",
    "MERLIN_FLY_STREAM_V2": "application/x-hdf5",
    "MERLIN_HDF5_BULK": "application/x-hdf5",
    "PANDA": "application/x-hdf5",
    "PILATUS_HDF5": "application/x-hdf5",
    "ROI_HDF5_FLY": "application/x-hdf5",
    "ROI_HDF51_FLY": "application/x-hdf5",
    "SIS_HDF51_FLY_STREAM_V1": "application/x-hdf5",
    "TIMEPIX4_BIN": "application/octet-stream",
    "TPX_HDF5": "application/x-hdf5",
    "NPY_SEQ": "multipart/related;type=application/x-npy",
    "SIS_HDF51": "application/x-hdf5",
    "XIA_XMAP_HDF5": "application/x-hdf5;type=xia-xmap",
    "XIAXMAP": "application/x-hdf5;type=xia-xmap",
    "XPS3_FLY": "application/x-hdf5",
    "XSP3": "application/x-hdf5",  # noqa: E501  iss_patches:ISSXspress3HDF5Handler, area_detector_handlers.handlers:Xspress3HDF5Handler
    "XSP3_BULK": "application/x-hdf5",
    "XSP3_FLY": "application/x-hdf5",
    "XSP3_STEP": "application/x-hdf5",  # noqa: E501  databroker.assets.handlers:Xspress3HDF5Handler, area_detector_handlers.handlers:Xspress3HDF5Handler
    "XSP3X": "application/x-hdf5",
}

# Define document-specific patches to be applied before sending them to TiledWriter

def patch_descriptor(doc):
    # Add more specific numpy-style data type, "dtype_str", if not present.
    if "usbcam1_image" in doc["data_keys"]:
        doc["data_keys"]["usbcam1_image"]["dtype_str"] = "|u1"
    if "usbcam2_image" in doc["data_keys"]:
        doc["data_keys"]["usbcam2_image"]["dtype_str"] = "|u1"
    if "xascam_image" in doc["data_keys"]:
        doc["data_keys"]["xascam_image"]["dtype_str"] = "|u1"
    if "xrdcam_image" in doc["data_keys"]:
        doc["data_keys"]["xrdcam_image"]["dtype_str"] = "|u1"
    if "anacam_image" in doc["data_keys"]:
        doc["data_keys"]["anacam_image"]["dtype_str"] = "|u1"
    for i in range(1, 5):
        if f"4-element SDD_channel0{i}" in doc["data_keys"]:
            doc["data_keys"][f"4-element SDD_channel0{i}"]["dtype_str"] = "<f8"

    # Ensure dtype_str has the proper numpy format (to pass the EventModel validator)
    for key, val in doc["data_keys"].items():
        if "dtype_str" in val:
            val["dtype_str"] = numpy.dtype(val["dtype_str"]).str
        if (("xs_channel" in key) or ("4-element SDD_channel" in key)) and (len(val.get("shape", [])) == 1):
            val["shape"] = [1, *val["shape"]]
        val["shape"] = tuple(map(lambda x: max(x, 0), val.get("shape", [])))

    return doc

def patch_resource(doc):

    kwargs = doc.get("resource_kwargs", {})

    # Fix the resource path
    root = doc.get("root", "")
    if not doc["resource_path"].startswith(root):
        doc["resource_path"] = os.path.join(root, doc["resource_path"])
    doc["root"] = ""

    doc["resource_path"] = doc["resource_path"].replace("/nsls2/data3/bmm", "/nsls2/data/bmm")

    # Fix the template string
    if not kwargs.get("template") and ("%" in doc["resource_path"]):
        doc["resource_path"], kwargs["template"] = doc["resource_path"].split("%", 1)
        kwargs["template"] = "%" + kwargs["template"]
    
    if "JPEG" in doc.get("spec", ""):
        kwargs["template"] = kwargs.get("template", "")
        kwargs["filename"] = kwargs.get("filename", "")
        kwargs["template"] = "/" + kwargs["template"].lstrip("/")    # Ensure leading slash

    # Single HDF5 should not be templated. Compile template and set to the first file in the sequence
    if "HDF5" in doc.get("spec", ""):
        if template := kwargs.pop("template", None):
            compiled = MultipartRelatedConsolidator._compile_template(template, kwargs.pop("filename", ""))
            doc["resource_path"] = os.path.join(doc["resource_path"], compiled.format(0))

    # Fix or add resource parameters
    if doc.get("spec") in ["XSP3", "XSP3X", "XSP3_FLY"]:
        kwargs.update({"dataset": 'entry/instrument/detector/data', "chunk_shape": (1, ), "join_method": "concat"})
    elif doc.get("spec") in {"BEAMLINE_WEBCAM", "BMM_USBCAM", "BMM_XAS_WEBCAM", "BMM_XRD_WEBCAM", "BMM_ANALOG_CAMERA", "BMM_JPEG_HANDLER"}:
        kwargs.update({"join_method": "stack"})     # To ensure that the leading dimension in stacked JPEGs is the number of files
        kwargs["template"] = kwargs.get("template", "")
    elif doc.get("spec") in ["AD_HDF5", "AD_HDF5_SWMR_STREAM", "AD_HDF5_SWMR_SLICE", "AD_HDF5_SWMR", "PIL100k_HDF5"]:
        kwargs.update({"dataset": 'entry/instrument/detector/data', "join_method": "stack"})

    return doc

def patch_datum(doc):
    kwargs = doc.get("datum_kwargs", {})
    if "channel" in kwargs:
        # databroker.assets.handlers.Xspress3HDF5Handler --- general case
        kwargs["dataset"] = "entry/instrument/detector/data"
        channel = kwargs["channel"]
        kwargs["slice"] = f"(:,{channel-1},:)"
        kwargs["squeeze"] = True

    return doc

# Initialize the Tiled client and TiledWriter
tiled_writing_client_sql = from_uri("https://tiled.nsls2.bnl.gov",
                                    api_key=os.environ["TILED_BLUESKY_WRITING_API_KEY_BMM"])['bmm/migration']
tiled_writing_client_sql.context.http_client.headers['tiled-qos'] = 'acquisition'
tw = TiledWriter(client = tiled_writing_client_sql,
                 backup_directory="/tmp/tiled_backup",   # Might conflict with TiledInserter -- need to test
                 patches = {"descriptor": patch_descriptor,
                            "resource": patch_resource,
                            "datum": patch_datum},
                 spec_to_mimetype= MIMETYPE_LOOKUP,
                 batch_size=1024,  # Set to 1 to enable streaming of Datum documents -- can be slower
                 )

# Thread-safe wrapper for TiledWriter
tw = BufferingWrapper(tw)

# Subscribe the TiledWriter
RE.md["tiled_access_tags"] = (RE.md["data_session"],)
RE.subscribe(tw)
