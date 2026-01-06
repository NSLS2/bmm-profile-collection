
import os, time

try:
    from bluesky_queueserver import is_re_worker_active
except ImportError:
    # TODO: delete this when 'bluesky_queueserver' is distributed as part of collection environment
    def is_re_worker_active():
        return False

#if is_re_worker_active():
#    from nslsii import _read_bluesky_kafka_config_file
#else:
from nslsii.kafka_utils import _read_bluesky_kafka_config_file
    
from bluesky_kafka.produce import BasicProducer

from bmm_tools.tools.misc     import now
from bmm_tools.tools.messages import warning_msg, bold_msg, whisper, error_msg
from bmm_tools.tools.md import proposal_base

from BMM.workspace import rkvs

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

kafka_config = _read_bluesky_kafka_config_file(config_file_path="/etc/bluesky/kafka.yml")

producer = BasicProducer(bootstrap_servers=kafka_config['bootstrap_servers'],
                         topic='bmm.test',
                         producer_config=kafka_config["runengine_producer_config"],
                         key='abcdef'
)


def kafka_message(message):
    '''Broadcast a message to kafka on the private BMM channel.

    For all BMM workers, the message is a dict.  See worker
    documentation for details.

    '''
    producer.produce(['bmm', message])


# Maintenance of kafka output
def close_line_plots():
    kafka_message({'close': 'line'})

def close_plots():
    kafka_message({'close': 'all'})

def kafka_verbose(onoff=False):
    kafka_message({'verbose': onoff})

# this is awkward.  it only works on fully qualified paths to
# something in Workspace or on a filename relative to user's Workspace
# This needs a file selection dialog!
def preserve(fname, target=None):
    '''Safely copy a file from your workspace to your proposal folder.
    '''
    if target is None:
        target = proposal_base()
    fullname = os.path.join(user_ns['BMMuser'].workspace, fname)
    if os.path.isfile(fullname):
        print(f'Copying {fname} to {target}')
        kafka_message({'copy': True,
                       'file': fullname,
                       'target': target})
    else:
        warning_msg(f"There is not a file called {fname} in {user_ns['BMMuser'].workspace}.")


def regenerate_file(uid, fname=None):
    '''Regenerate an XDI file for an XAS measurement given a UID.'''
    kafka_message({'xasxdi': True, 'uid': uid, 'filename': fname})


from dateutil.parser import parse
def is_date(string):
    try: 
        parse(string)
        return True
    except:
        return False
    
def regenerate_every_xas_scan(gup=None, since=None, until=None):
    '''Regenerate all XAS scans for a given experiment.

    arguments
    =========
    gup [str or int]
      The GU number either as a string or an integer

    since [date string of the form YYYY-MM-DD]
      The starting date of the search.  If not provided, 2018-01-01 will be used

    until [date string of the form YYYY-MM-DD]
      The ending date of the search. If not provided, the current date will be used

    '''
    if gup is None:
        return []
    if type(gup) is int:
        gup = str(gup)
    if gup.startswith('pass-'):
        gup.replace('pass-', '')
    if since is None:
        since = '2018-01-01'
    if is_date(since) is False:
        error_msg(f'"{since}" is not an interpretable date string.  Try specifying your date in the form YYYY-MM-DD')
        return
    if until is None:
        until = now().split('T')[0]
    if is_date(until) is False:
        error_msg(f'"{until}" is not an interpretable date string.  Try specifying your date in the form YYYY-MM-DD')
        return
    kafka_message({'everyxas': True, 'gup': gup, 'since': since, 'until': until})
    bold_msg('This will take some time to complete.')
    whisper('Progress can be monitored in the terminal window displaying the Kafka file manager.')



def file_exists(folder=None, filename=None, start=1, stop=2, maxtries=15, number=True, verbose=False):
    '''Determine if a file of the specified filename exists in a specified
    folder in the proposals directory.

    This sends a message over kafka asking the file manager worker to
    search for the file.  The worker posts "true" or "false" to redis.
    This function sets that redis key to "None" and polls the
    appropriate redis key for a "true"/"false" value.
   
    arguments
    =========
    folder: (str)
      folder in proposal directory to probe [proposal_base()]

    filename: (str)
      filename to check, i.e. filename with extension but without path

    start: (int)
      start of extension number range to check

    stop: (int)
      end of extension number range to check

    maxtries: (int)
      maximum number of attempts to read before giving up and returning None

    number: (bool)
      if True, search for numbered extensions.  if False, search for filename as specified

    verbose: (bool)
      if True, be noisy as we wait for a result

    '''
    if folder is None:
        folder = proposal_base()
    if filename is None:
        error_msg('No filename supplied to file_exists')
        return(None)
    #rkvs = user_ns['rkvs']
    rkvs.set('BMM:file_exists', 'None')
    kafka_message({'file_exists': True, 'folder': folder, 'filename': filename, 'start': start, 'stop': stop, 'number': number})
    answer = rkvs.get('BMM:file_exists').decode('utf8')
    count = 0
    if verbose: print(f"{count = }, {answer = }")
    while answer == 'None':
        time.sleep(0.1)
        answer = rkvs.get('BMM:file_exists').decode('utf8')
        count += 1
        if verbose: print(f"{count = }, {answer = }")
        if count > maxtries:
            return(None)
    if answer == 'true':
        return True
    else:
        return False
    
    
