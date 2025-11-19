import subprocess
import os

from BMMCommon.tools.messages import *  # error_msg et al. + boxedtext

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from BMM.user_ns.bmm import BMMuser


# def run_athena():
#     os.environ['DEMETER_FORCE_IFEFFIT'] = '1' 
#     subprocess.Popen(["dathena"], stderr=subprocess.DEVNULL)
    
def run_hephaestus():
    os.environ['DEMETER_FORCE_IFEFFIT'] = '1' 
    subprocess.Popen(["dhephaestus"], stderr=subprocess.DEVNULL)


