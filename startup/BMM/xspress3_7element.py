import sys
from BMM.xspress3 import BMMXspress3DetectorBase

# this was a relevant check when the Xspress3 was first commissioned
# at BMM.  Not so much any more.  Aug 27, 2024
if sys.version_info[1] == 9:
    from nslsii.areadetector.xspress3 import build_detector_class
else:
    from nslsii.areadetector.xspress3 import build_xspress3_class

################################################################################
# Notes:
#
# Before every count or scan, must explicitly set the number of points in the
# measurement:
#   xs.total_points.put(5) 
#
# This means that Xspress3 will require its own count plan
# also that a linescan or xafs scan must set total_points up front

# JOSH: I wish someone had put that note in nslsii.detector.xspress3.py
class BMMXspress3Detector_7Element_Base(BMMXspress3DetectorBase):
    '''Subclass of BMMXspress3DetectorBase specific to the 7-element interface.
    '''
    ...

if sys.version_info[1] == 9:
    BMMXspress3Detector_7Element = build_detector_class(
        channel_numbers=(1, 2, 3, 4, 5, 6, 7),
        mcaroi_numbers=range(1, 21),
        detector_parent_classes=(BMMXspress3Detector_7Element_Base, )
    )
else:
    BMMXspress3Detector_7Element = build_xspress3_class(
        channel_numbers=(1, 2, 3, 4, 5, 6, 7),
        mcaroi_numbers=range(1, 21),
        image_data_key="xrf",
        xspress3_parent_classes=(BMMXspress3Detector_7Element_Base, ),
    )
