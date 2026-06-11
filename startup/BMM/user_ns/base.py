import nslsii
from nslsii.utils import open_redis_client
import os, time, datetime, configparser
from collections import deque

from event_model import pack_datum_page
from bluesky.plan_stubs import mv, mvr, sleep
from databroker import Broker
from tiled.client import from_uri, show_logs

from rich import print as cprint

import bmm_tools.tools.md


try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False

## the intent here is to return $HOME/.profile_collection/startup
#startup_dir = os.path.split(os.path.split(os.path.split(__file__)[0])[0])[0]
startup_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    
cfile = os.path.join(startup_dir, "BMM_configuration.ini")
profile_configuration = configparser.ConfigParser(interpolation=None)
def reload_profile_configuration():
    profile_configuration.read_file(open(cfile))
reload_profile_configuration()
    
os.environ['BLUESKY_KAFKA_BOOTSTRAP_SERVERS'] = profile_configuration.get('services', 'kafka')

WORKSPACE = profile_configuration.get('services', 'workspace')
PROPOSALS = profile_configuration.get('services', 'proposals')
BMM       = profile_configuration.get('services', 'bmm')
    
#print('just read config file', flush=True)


from redis_json_dict import RedisJSONDict
import redis

uns_dict = dict()

class dummy_broker:
    name = 'bmm'
    
    def insert(self, *args):
        pass

dummy = dummy_broker()

use_kafka = True
#print('about to call configure_base', flush=True)
#print(f"URL: {profile_configuration.get('services', 'nsls2_redis')}")
#print(f"Port: {profile_configuration.get('services', 'redis_port')}")
#print(f"use_ssl: {profile_configuration.get('services', 'redis_ssl')}")

if not is_re_worker_active():
    ip = get_ipython()
    nslsii.configure_base(ip.user_ns,
                          'bmm',
                          configure_logging=True,
                          publish_documents_with_kafka=use_kafka,
                          redis_url=profile_configuration.get('services', 'nsls2_redis'),
                          redis_port=profile_configuration.get('services', 'redis_port'),
                          redis_ssl=profile_configuration.get('services', 'redis_ssl'),
                          redis_db=1
    )
    ip.log.setLevel('ERROR')
    RE  = ip.user_ns['RE']
    sd  = ip.user_ns['sd']
    bec = ip.user_ns['bec']
else:
    nslsii.configure_base(uns_dict,
                          dummy,
                          configure_logging=True,
                          publish_documents_with_kafka=True,
                          redis_url=profile_configuration.get('services', 'nsls2_redis'),
                          redis_port=profile_configuration.get('services', 'redis_port'),
                          redis_ssl=profile_configuration.get('services', 'redis_ssl'),
                          redis_db=1
    )
    RE  = uns_dict['RE']
    nslsii.configure_kafka_publisher(RE, "bmm")
    sd  = uns_dict['sd']
    bec = uns_dict['bec']
RE.unsubscribe(0)  # remove databroker, which was subscribed first by configure_base
tiled_writing_client = from_uri(profile_configuration.get('services', 'tiled'),
                                api_key=os.environ["TILED_BLUESKY_WRITING_API_KEY_BMM"])
tiled_writing_client.context.http_client.headers['tiled-qos'] = 'acquisition'

datum_docs_cache = deque()
def create_datum_page_cb(name, doc):
    if name == "datum":
        datum_docs_cache.append(doc)
        return

    if len(datum_docs_cache) > 0:
        datum_page = pack_datum_page(*datum_docs_cache)
        datum_docs_cache.clear()
        post_document("datum_page", datum_page)

    post_document(name, doc)


def post_document(name, doc):
    #tz = time.monotonic()

    ATTEMPTS = 6
    error = None
    #cprint(f'[orange1]{name}[/orange1]')
    for attempt in range(ATTEMPTS):
        try:
            tiled_writing_client.post_document(name, doc)
        except Exception as exc:
            print("(BMM local warning) Document saving failure:", repr(exc))
            error = exc
        else:
            break
        print(f'(BMM local warning) sleeping {2**attempt} seconds before trying again...')
        time.sleep(2**attempt)
    else:
        # out of attempts
        print('***************************************************')
        print('(BMM local warning) ')
        print('One last try to connect to tiled.  Wating 2 minutes')
        print(f'Begin sleeping for 2 minutes at {datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")}')
        print('***************************************************')
        time.sleep(120)
        try:
            tiled_writing_client.post_document(name, doc)
        except Exception as exc:
            print("(BMM local warning) Document saving failure:", repr(exc))
            print("Likeliest cause is a network failure or a Tiled service failure.  Contact beamline staff.")
            error = exc
            raise error
        else:
            pass

    #print(f"post_document timing: {time.monotonic() - tz:.3}\n")
    
# RE.subscribe(post_document)
RE.subscribe(create_datum_page_cb)

# this prefix needs to be the same (but with a dash) as the call to sync_experiment in user.py

#RE.md = open_redis_client(profile_configuration.get('services', 'nsls2_redis'),
#                          profile_configuration.get('services', 'redis_port'),
#                          profile_configuration.get('services', 'redis_ssl'),
#                          redis_db=1)
bmm_tools.tools.md.common_re = RE
bmm_tools.tools.md.common_md = RE.md
    
bec.disable_plots()
bec.disable_baseline()

import ophyd
ophyd.EpicsSignal.set_defaults(timeout=10, connection_timeout=10)

#from databroker.core import SingleRunCache

from bluesky.utils import ts_msg_hook
RE.msg_hook = ts_msg_hook

bmm_catalog = None

print()
if not is_re_worker_active():
    cprint('[cyan]Connecting to Tiled: bmm_catalog, db[/cyan]')
    from tiled.client import from_profile, from_uri
    bmm_catalog = from_profile('bmm')
    bmm_catalog.context.http_client.headers['tiled-qos'] = 'acquisition'
    ##bmm_catalog = from_uri('https://tiled.nsls2.bnl.gov/api/v1/metadata/bmm/migration')
    db = Broker(bmm_catalog)


cprint('[cyan]Subscribing to Publisher[/cyan]')
from bluesky.callbacks.zmq import Publisher
publisher = Publisher('localhost:5577')
RE.subscribe(publisher)


def print_docs_to_stdout(name, doc):
    print("====================================")
    print(f"{name = }")
    print(f"{doc = }")
    print("====================================")


#RE.subscribe(print_docs_to_stdout)
