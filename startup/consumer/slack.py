
import os, datetime, configparser
from tools import echo_slack, profile_configuration
from nslsii.utils import open_redis_client

#-------- fetch Slack configuration --------------------------------
use_nsls2_slack = profile_configuration.getboolean('slack', 'use_nsls2')
use_bmm_slack = profile_configuration.getboolean('slack', 'use_bmm')

from bmm_tools.slack.bmmbot import BMMbot
bmmbot = BMMbot()
bmmbot._bmmbot_secret = profile_configuration.get('slack', 'bmmbot_secret')
bmmbot._redis_client = open_redis_client(profile_configuration.get('services', 'nsls2_redis'),
                                         profile_configuration.get('services', 'redis_port'),
                                         profile_configuration.get('services', 'redis_ssl'),
                                         redis_db=1)
#bmmbot._redis_client = redis.Redis(host=profile_configuration.get('services', 'nsls2_redis'))
bmmbot._pass_api = profile_configuration.get('services', 'pass_api') + "/{pass_id}/slack-channels"
bmmbot.refresh_channel()

#-------------------------------------------------------------------


#-------- soon to be deprecated Slack config -----------------------
slack_secret = profile_configuration.get('slack', 'slack_secret')
import json
from urllib import request, parse
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
try:
    with open(slack_secret, "r") as f:
        default_slack_channel = f.read().replace('\n','')
except:
    print(error_msg('\t\t\tslack_secret file not found!'))
#-------------------------------------------------------------------

def refresh_slack():
    bmmbot.refresh_channel()

def describe_slack():
    bmmbot.describe()

def test_slack():
    bmmbot.test()

    
def post_to_slack(text, rid=None):
    ## BMM's own Slack channel, soon to be deprecated
    if use_bmm_slack is True:
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
            
    ## NSLS2 Slack channel
    if use_nsls2_slack:
        bmmbot.post(text)
        
    ## record Slack timeline
    echo_slack(text=text, img=None, icon='message', rid=rid)


## Simple but useful guide to configuring a slack app:        
## https://hamzaafridi.com/2019/11/03/sending-a-file-to-a-slack-channel-using-api/
def img_to_slack(imagefile, title='', measurement='xafs'):
    ## BMM's own Slack channel, soon to be deprecated
    if use_bmm_slack:
        token_file = os.path.join(profile_configuration.get('slack', 'image_uploader'))
        try:
            with open(token_file, "r") as f:
                token = f.read().replace('\n','')
        except:
            post_to_slack(f'failed to post image: {imagefile}')
            return()
        client = WebClient(token=token)
        #client = WebClient(token=os.environ['SLACK_API_TOKEN'])
        try:
            response = client.files_upload_v2(channel='C016GHBFHTM',
                                              file=imagefile,
                                              title=title)
            # #beamtime channel ID: C016GHBFHTM
            assert response["file"]  # the uploaded file
        except SlackApiError as e:
            post_to_slack(f'failed to post image: {imagefile}')
            # You will get a SlackApiError if "ok" is False
            assert e.response["ok"] is False
            assert e.response["error"]  # str like 'invalid_auth', 'channel_not_found'
            print(f"Got an error: {e.response['error']}")
        except Exception as em:
            print("EXCEPTION: " + str(em))
            post_to_slack(f'failed to post image: {imagefile}')
            
    ## NSLS2 Slack channel
    if use_nsls2_slack:
        bmmbot.image(fname=imagefile, title=title)

    ## record Slack timeline
    icon = 'plot'
    if imagefile.endswith('.jpg'): icon = 'camera'
    echo_slack(text=os.path.basename(imagefile), img=os.path.basename(imagefile), icon=icon, measurement=measurement)
