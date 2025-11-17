import logging, datetime, emojis
import os
from urllib import request, parse
import json, requests
from os import chmod
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from BMM.functions           import error_msg, warning_msg, go_msg, url_msg, bold_msg, verbosebold_msg, list_msg, disconnected_msg, info_msg, whisper
from BMM.kafka import kafka_message

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from BMM.user_ns.base import startup_dir, profile_configuration


#run_report(__file__, text='BMM-specific logging')

BMM_logger          = logging.getLogger('BMM_logger')
BMM_logger.handlers = []

BMM_formatter       = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s\n%(message)s')


try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False


## how to get hostname: os.uname()[1]

BMM_log_master_file = os.path.join(os.environ['HOME'], 'Data', 'BMM_master.log')
if not os.path.isdir(os.path.join(os.environ['HOME'], 'Data')):
    os.makedirs(os.path.join(os.environ['HOME'], 'Data'))
if not os.path.isfile(BMM_log_master_file):
    os.mknod(BMM_log_master_file)
if os.path.isfile(BMM_log_master_file):
    chmod(BMM_log_master_file, 0o644)
    BMM_log_master = logging.FileHandler(BMM_log_master_file)
    BMM_log_master.setFormatter(BMM_formatter)
    BMM_logger.addHandler(BMM_log_master)
    chmod(BMM_log_master_file, 0o444)

LUSTRE_ROOT_BMM = '/nsls2/data3/bmm'
BMM_lustre_log_file = os.path.join(LUSTRE_ROOT_BMM, 'XAS', 'BMM_master.log')
if os.path.isdir(LUSTRE_ROOT_BMM):
    if not os.path.isfile(BMM_lustre_log_file):
        basedir = os.path.dirname(BMM_lustre_log_file)
        if not os.path.exists(basedir):
            os.makedirs(basedir)
        os.mknod(BMM_lustre_log_file)
    if os.path.isfile(BMM_lustre_log_file):
        chmod(BMM_lustre_log_file, 0o644)
        BMM_log_lustre = logging.FileHandler(BMM_lustre_log_file)
        BMM_log_lustre.setFormatter(BMM_formatter)
        BMM_logger.addHandler(BMM_log_lustre)
    

#------------------------------------------------------------------------------------------
# stolen from QAS, see message from Max, #discuss-bluesky, thread starting 8:28 am Thursday January 25th 2024
# Try to capture 'core dump' reasons.
if not is_re_worker_active():
    import faulthandler, sys
    from pprint import pprint, pformat
    faulthandler.enable()


    def audit(event, args):
        if event == "open":
            BMM_logger.debug(f"Opening file: {args}")

    sys.addaudithook(audit)        
#------------------------------------------------------------------------------------------


BMM_logger.setLevel(logging.WARNING) # DEBUG

BMM_log_user = None

## this is intended to be a log file in the experiment folder
## thus all scans, etc. relevant to the experiment will be logged with the data
## call this at the beginning of the beamtime
def BMM_user_log(filename):
    BMM_log_user = logging.FileHandler(filename)
    BMM_log_user.setFormatter(BMM_formatter)
    BMM_logger.addHandler(BMM_log_user)

## remove all but the master log from the list of handlers
def BMM_unset_user_log():
    BMM_logger.handlers = []
    BMM_logger.addHandler(BMM_log_master)

## use this command to properly format the log message, manage file permissions, etc
def BMM_log_info(message):
    chmod(BMM_log_master_file, 0o644)
    chmod(BMM_lustre_log_file, 0o644)
    entry = ''
    for line in message.split('\n'):
        entry += '    ' + line + '\n'
    BMM_logger.info(entry)
    chmod(BMM_log_master_file, 0o444)
    chmod(BMM_lustre_log_file, 0o444)


## small effort to obfuscate the web hook URL, which is secret-ish.  See:
##   https://api.slack.com/messaging/webhooks#create_a_webhook
use_bmm_slack = profile_configuration.getboolean('slack', 'use_bmm')
slack_secret = profile_configuration.get('slack', 'slack_secret')
try:
    with open(slack_secret, "r") as f:
        default_slack_channel = f.read().replace('\n','')
except:
    error_msg('\t\t\tslack_secret file not found!')


use_bmm_slack = profile_configuration.getboolean('slack', 'use_bmm')
use_nsls2_slack = profile_configuration.getboolean('slack', 'use_nsls2')
bmmbot_secret = profile_configuration.get('slack', 'bmmbot_secret') # '/nsls2/data3/bmm/XAS/secrets/bmmbot_secret'
        

    
def post_to_slack(text):
    BMMuser = user_ns['BMMuser']
    if use_bmm_slack:
        try:
            channel = BMMuser.slack_channel
        except:
            channel = default_slack_channel
        if channel is None or channel == '':
            channel = default_slack_channel
        post = {"text": "{0}".format(text)}
        try:
            json_data = json.dumps(post)
            req = request.Request(channel,
                                  data=json_data.encode('ascii'),
                                  headers={'Content-Type': 'application/json'}) 
            resp = request.urlopen(req)
        except Exception as em:
            print("EXCEPTION: " + str(em))
            print(f'slack_secret = {slack_secret}')
    if use_nsls2_slack:
        BMMuser.bmmbot.post(text)
        
        
def report(text, level=None, slack=False, rid=None):
    '''Print a string to:
      * the log file
      * the screen
      * the BMM beamtime slack channel
      * the NSLS2 slack channel

    Report level decorations on screen:

      * 'error' (red)
      * 'warning' (yellow)
      * 'info' (brown)
      * 'url' (un-decorated)
      * 'bold' (bright white)
      * 'verbosebold' (bright cyan)
      * 'list' (cyan)
      * 'disconnected' (purple)
      * 'whisper' (gray)

    not matching a report level will be un-decorated
    '''
    BMMuser = user_ns['BMMuser']
    BMM_log_info(text)
    screen = emojis.encode(text)
    if level is not None: # test that level is sensible...
        if level == 'error':
            error_msg(screen)
        elif level == 'warning':
            warning_msg(screen)
        elif level == 'info':
            info_msg(screen)
        elif level == 'url':
            url_msg(screen)
        elif level == 'bold':
            bold_msg(screen)
        elif level == 'verbosebold':
            verbosebold_msg(screen)
        elif level == 'disconnected':
            disconnected_msg(screen)
        elif level == 'list':
            list_msg(screen)
        elif level == 'whisper':
            whisper(screen)
        else:
            print(screen)
    else:
        print(screen)
    if slack:
        kafka_message({'echoslack': True,
                       'text': text,
                       'img': None,
                       'icon': 'message',
                       'rid': rid})

        

######################################################################################
# here is an example of what a message tuple looks like when moving a motor          #
# (each item in the tuple is on it's own line):                                      #
#     set:                                                                           #
#     (XAFSEpicsMotor(prefix='XF:06BMA-BI{XAFS-Ax:LinX}Mtr', name='xafs_linx', ... ) #
#     (-91.5999475,),                                                                #
#     {'group': '8c8df020-23aa-451e-b411-c427bc80b375'}                              #
######################################################################################
def BMM_msg_hook(msg):
    '''
    BMM-specific function for RE.msg_hook
    '''
    #print(msg)
    if msg[0] == 'set' and msg[2][0] is not None:
        if 'EpicsMotor' in str(type(msg[1])):
            report('Moving %s to %.3f'  % (msg[1].name, msg[2][0]))
        elif 'EpicsSignal' in str(type(msg[1])):
            report('Setting %s to %.3f' % (msg[1].name, msg[2][0]), 'whisper')
        elif 'LockedDwell' in str(type(msg[1])):
            report('Setting %s to %.3f' % (msg[1].name, msg[2][0]), 'whisper')
        elif 'PseudoSingle' in str(type(msg[1])):
            report('Moving %s to %.3f'  % (msg[1].name, msg[2][0]))
    # else:
    #     report('something went wrong with the last data point')
    #     print(msg)

