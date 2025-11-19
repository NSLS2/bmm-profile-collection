from ophyd import EpicsSignalRO
import os, subprocess, shutil, socket
import redis
from BMMCommon.tools.messages import *  # error_msg et al. + boxedtext
#import BMM.functions  #from BMM.functions import verbosebold_msg, error_msg
from BMM.user_ns.base import startup_dir, profile_configuration


if not os.environ.get('AZURE_TESTING'):
    redis_host = profile_configuration.get('services', 'bmm_redis')
else:
    redis_host = '127.0.0.1'


class NoRedis():
    def set(self, thing, otherthing):
        return None
    def get(self, thing):
        return None
    
###################################################################
# things that are configurable                                    #
###################################################################
try:
    rkvs = redis.Redis(host=redis_host, port=6379, db=0)
except:
    rkvs = NoRedis()
LUSTRE_ROOT = '/nsls2/data3'
LUSTRE_ROOT_BMM = '/nsls2/data3/bmm'
SECRETS = os.path.join(LUSTRE_ROOT_BMM, 'XAS', 'secrets')
SECRET_FILES = ('slack_secret', 'image_uploader_token', 'bmmbot_secret')
REDISVAR="BMM:scan:type"
###################################################################

rkvs.set('BMM:scan:type', 'idle')

def rkvs_keys(printed=True):
    '''Convert rkvs.keys() into a list of normal strings

    With printed=True, write a table of keys and values to the screen

    With printed=False, return a list containing keys as normal strings
    '''
    keys = sorted(list(x.decode('UTF-8') for x in rkvs.keys()))
    if printed is True:
        for k in keys:
            if rkvs.type(k) == b'string':
                print(f'{k:25} {rkvs.get(k).decode("UTF-8")}')
            elif rkvs.type(k) == b'list':
                this = ' '.join(x.decode('UTF-8') for x in rkvs.lrange(k, 0, -1))
                print(f'{k:25} {this}')
            else:
                #print(f'{k:25} {rkvs.get(k)}')
                pass
        return()
    else:
        return(keys)
    
CHECK = '\u2714'
TAB   = '\t\t\t'
wa    = EpicsSignalRO('XF:06BM-CT{}Prmt:RemoteExp-Sel', name='write_access')

def initialize_workspace():
    '''Perform a series of checks to see if the workspace on this computer
    is set up as expected by the BMM data collection profile.  This
    includes checks that:
      * channel access is enabled
      * servers can be seen on the LAN
      * various directories exist
      * Lustre mounted
      * a redis server is available
      * certain git repositories are cloned onto this computer
      * authentication files for Slack are available.

    For most checks, a failure triggers a corrective action, if
    possible.  Some failures print a warning to screen, with no
    corrective action.

    This is, essentially, a deployment verification that is run every
    time bsui starts.

    Additional acceptance testing is done throughout the profile
    start-up, including steps to 
      * check connectivity and homed state of motors
      * availability and configuration of detectors.

    '''
    verbosebold_msg('''

================================================================
bsui profile for NIST's Beamline for Materials Measurement (6BM)
================================================================

Verifying workspace on this computer ...''')
    check_workstation_access()
    check_lan()
    check_profile_branch()
    initialize_data_directories()
    #initialize_lustre()
    initialize_secrets()
    initialize_redis()

    ## deprecated steps
    #initialize_gdrive()
    #initialize_beamline_configuration()
    #initialize_ssh()
    
def check_workstation_access():
    if wa.get() == 0:
        error_msg(f'{TAB}*** Uh oh!  The beamline is not enabled for write access to PVs!')
        print(f'{TAB}    A beamline staff person needs to do:')
        print(f'{TAB}       caput XF:06BM-CT{{}}Prmt:RemoteExp-Sel 1')
        print(f'{TAB}    then restart bsui')
        print(f'{TAB}    (Now issuing a command that will fail and return to the command line.)')
        ## the next line is intended to trigger an immediate error and return to the IPython command line
        wa.put(1)
    else:
        print(f'{TAB}Channel access enabled: {CHECK}')
        
def check_lan():
    freakout = 0
    for host in ('ioc2', 'disp1'):
        response = os.system(f"ping -q -c 1 xf06bm-{host} > /dev/null")
        if response != 0:
            error_msg(f'{TAB}*** Uh oh!  xf06bm-{host} is not responding to a ping!')
            freakout = 1;

    if freakout == 1:
        print(f'{TAB}    You may need to reboot the missing server(s).')
        print(f'{TAB}    Consult the DSSI support team for help.')
        print(f'{TAB}    (Now issuing a command that will fail and return to the command line.)')
        ## the next line is intended to trigger an immediate error and return to the IPython command line
        wa.put(1)
    else:
        print(f'{TAB}Servers on LAN are accessible: {CHECK}')
        
        
def check_profile_branch():
    here = os.getcwd()
    #os.chdir(os.path.dirname(startup_dir))
    os.chdir(profile_configuration.get('services', 'startup'))
    try:
        branch = subprocess.check_output(['git', 'branch', '--show-current']).decode("utf-8")[:-1]
    except subprocess.CalledProcessError:
        branch = "not a git repository"
    print(f'{TAB}Using profile branch "{branch}": {CHECK}')
    os.chdir(here)
    
def check_directory(dir, desc):
    if os.path.isdir(dir):
        print(f'{TAB}{desc.capitalize()} directory {dir}: {CHECK}')
        return True
    else:
        print(f'{TAB}Making {desc} directory {dir}')
        os.mkdir(dir)
        return False

    
def initialize_data_directories():
    '''Verify that a workspace directory is available under the home of the
    user running bsui.  Then verify that several subdirectories exist.
    Create any missing directories.

    '''
    workspace = profile_configuration.get('services', 'workspace')
    check_directory(workspace, 'workspace')
    for sub in ('Staff', 'Visitors'):
        folder = f'{workspace}/{sub}'
        check_directory(folder, 'data')


def initialize_lustre():
    '''Check if a the Lustre mount point for data directories is mounted.
    If not, complain on screen.

    '''
    if os.path.ismount(LUSTRE_ROOT_BMM):
        print(f'{TAB}Found Lustre mount point: {CHECK}')
    else:
        error_msg(f'{TAB}*** Uh oh! Lustre is not mounted!')
        print(f'{TAB}    Consult the DSSI support team for help.')
        print(f'{TAB}    (Now issuing a command that will fail and return to the command line.)')
        ## the next line is intended to trigger an immediate error and return to the IPython command line
        wa.put(1)



def initialize_secrets():
    '''Check that the Slack secret files are in their expected locations.
    If not, copy them from Lustre at /nsls2/data3/bmm/XAS/secrets.

    '''
    STARTUP = os.path.join(startup_dir, 'BMM')
    for fname in SECRET_FILES:
        if os.path.isfile(os.path.join(STARTUP, fname)):
            print(f'{TAB}Found {fname} file: {CHECK}')
        else:
            try:
                shutil.copyfile(os.path.join(SECRETS, fname), os.path.join(STARTUP, fname))
                print(f'{TAB}Copied {fname} file')
            except Exception as e:
                print(e)
                error_msg(f'{TAB}Failed to copy {os.path.join(SECRETS, fname)}!')

                
def initialize_redis():
    '''Check to see if a successful response can be obtained from a redis
    server.  If not, complain on screen.

    '''
    if rkvs.get(REDISVAR) is not None:
        print(f'{TAB}Found Redis server: {CHECK}')
    else:
        error_msg(f'{TAB}*** Uh oh! Did not find redis server')
        print(f'{TAB}    A beamline staff person needs to log onto xf06bm-ioc2:')
        print(f'{TAB}       dzdo systemctl start redis')
        print(f'{TAB}    then restart bsui')
        print(f'{TAB}    (Now issuing a command that will fail and return to the command line.)')
        ## the next line is intended to trigger an immediate error and return to the IPython command line
        wa.put(1)


def initialize_beamline_configuration():
    '''Check that a git directory exists beneath the home of the user
    running bsui.  Create the git directory and clone the
    BMM-beamline-configuration repository if absent.  If present, pull
    from the upstream repository to be sure the modes JSON file is up
    to date.

    DEPRECATED, relevant files moved into beamline profile

    '''
    GIT=f'{os.environ["HOME"]}/git'
    check_directory(GIT, 'git')
    BLC = f'{GIT}/BMM-beamline-configuration'
    existed = check_directory(BLC, 'git')
    here = os.getcwd()
    if existed:
        os.chdir(BLC)
        subprocess.run(['git', 'pull']) 
    else:
        os.chdir(GIT)
        subprocess.run(['git', 'clone', '-q', 'https://github.com/NSLS-II-BMM/BMM-beamline-configuration']) 
    os.chdir(here)


def initialize_ssh():
    '''Check to see if xf06bm-ws3 has an authorized ssh key from this
    computer.  If not, complain on screen.

    DEPRECATED

    '''
    if 'xf06bm-ws3' in socket.gethostname():
        print(f'{TAB}This is xf06bm-ws3, no ssh key needed: {CHECK}')
        return
    s = subprocess.run(['ssh', '-q', '-oBatchMode=yes', 'xf06bm@xf06bm-ws1', 'true'])
    if s.returncode == 0:
        print(f'{TAB}Key exists for xf06bm@xf06bm-ws3: {CHECK}')
    else:
        error_msg(f'{TAB}Key does not exist for xf06bm@xf06bm-ws1')


def ping(host):
    a=subprocess.run(["ping", "-c", '2', host], capture_output=True)
    if a.returncode == 0:
        return True
    else:
        return False

    
def check_linkam(linkam):
    from BMM.user_ns.instruments import WITH_LINKAM
    if WITH_LINKAM:
        if linkam.model == 'T96-S':
            print(f'{TAB}Linkam stage is available {CHECK}')
        else:
            error_msg(f'{TAB}Linkam stage is powered dawn or out of communication with IOC2')
    else:
        print(f'{TAB}Linkam stage is available {CHECK}')
    return

def check_lakeshore(lakeshore):
    from BMM.user_ns.instruments import WITH_LAKESHORE
    if WITH_LAKESHORE:
        was = lakeshore.units_sel.get()
        lakeshore.units_sel.put(0)
        if lakeshore.sample_a.get() == 0.0:
            error_msg(f'{TAB}LakeShore 331 is powered dawn or out of communication with IOC2')
        else:
            print(f'{TAB}LakeShore 331 is available {CHECK}')
        lakeshore.units_sel.put(was)
    else:
        whisper(f'{TAB}LakeShore 331 is unavailable')
    return
    
def check_biologic():
    ret = ping('xf06bm-biologic')
    if ret is True:
        print(f'{TAB}BioLogic potentiostat is available {CHECK}')
    else:
        error_msg(f'{TAB}BioLogic is not responding to pings (powered down or not on the network)')
    return
    
def check_electrometers():
    from BMM.user_ns.dwelltime import with_quadem, with_ic0, with_ic1, with_ic2
    hosts = {
        'em1': ['quadem1, old ion chamber signals,', with_quadem],
        # 'em2': ['quadem2, old ion chamber signals,', with_quadem],
        'ic1': ['ic0, the I0 detector,', with_ic0],
        'ic2': ['ic1, the It detector,', with_ic1],
        'ic3': ['ic2, the Ir detector,', with_ic2],
    }
    for h in hosts.keys():
        if hosts[h][1] is True:
            ret = ping(f'xf06bm-{h}')
            if ret is True:
                print(f'{TAB}{hosts[h][0]} is available {CHECK}')
            else:
                error_msg(f'{TAB}{hosts[h][0]} is not available')
    return
    
def check_xspress3(xs):
    ret = ping('xf06bm-xspress3')
    if ret is False:
        error_msg(f'{TAB}Xspress3 is not responding to pings')
        return
    if xs.connected is False:
        error_msg(f'{TAB}Xspress3 IOC is unavailable')
        return
    print(f'{TAB}XSpress3 server and its IOC are available {CHECK}')
    return

def check_diode():
    remote_sts = EpicsSignalRO('XF:06BM-CT{DIODE}RemoteIO-Sts', name='remote')
    local_sts  = EpicsSignalRO('XF:06BM-CT{DIODE}LocalBus-Sts', name='local')
    #heartbeat  = EpicsSignalRO('XF:06BM-CT{DIODE}CPU-Sts', name='heartbeat')
    ok = remote_sts.get() == 1 and local_sts.get() == 1
    if ok is True:
        print(f'{TAB}DIODE (sample spinners, XRD filters) is available {CHECK}')
    else:
        error_msg(f'{TAB}DIODE (sample spinners, XRD filters) is not available')
        
    
def check_instruments(linkam, lakeshore, xs):
    '''Run simple tests to see if connectivity exists to various
    instruments at BMM.  This is intended to be run very late in
    profile startup.
    
    linkam
       Probe for value of linkam.model, should be 'T96-S' if stage is connected,
       IOC is on, and ophyd device is connected.  This will help notice if kernel
       module on xf06bm-ioc2 allowing communication with Moxa ports is compiled 
       and loaded after a reboot or update.

    lakeshore
       Probe the temperature value of sensor A on the LakeShore 331
       with units set to K. If the result is non-zero, then IOC is on,
       and ophyd device is connected.  This will help notice is kernel
       module on xf06bm-ioc2 allowing communication with Moxa ports is
       compiled and loaded after a reboot or update.
       
    biologic
       Ping the potentiostat at xf06bm-biologic. This will verify that the
       potentiostat is powered up and on the network

    quadem1, ic0, ic1, ic2
       Ping these devices at xf06bm-em1, xf06bm-ic1, etc.  This will verify which 
       electrometer devices are on the network

    xspress3
       ping xf06bm-xspress3 and check xs.connected is True

    diode
       check that the local and remote buses report being up

    '''
    verbosebold_msg(f'\t\tverifying availability of instruments ...')
    check_linkam(linkam)
    check_lakeshore(lakeshore)
    check_biologic()
    check_electrometers()
    #check_xspress3(xs)
    check_diode()
    
