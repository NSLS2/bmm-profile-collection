import datetime
import signal
import pprint
import uuid
import sys
import os
import re
import time
import shutil
sys.path.append('/home/xf06bm/.ipython/bmm_tools/src/')
sys.path.append('/home/xf06bm/.ipython/profile_collection/startup')

from bluesky_kafka.consume import BasicConsumer
import nslsii
import nslsii.kafka_utils

from tools import echo_slack, next_index, file_exists, profile_configuration
from slack import img_to_slack, post_to_slack, refresh_slack, describe_slack, test_slack


from tiled.client import from_profile
bmm_catalog = from_profile('bmm')


import redis
if not os.environ.get('AZURE_TESTING'):
    redis_host = profile_configuration.get('services', 'bmm_redis')
else:
    redis_host = '127.0.0.1'
class NoRedis():
    def set(self, thing, otherthing):
        return None
    def get(self, thing):
        return None
try:
    rkvs = redis.Redis(host=redis_host, port=6379, db=0)
except:
    rkvs = NoRedis()




# legible screen output
from pygments import highlight
from pygments.lexers import PythonLexer, HtmlLexer
from pygments.formatters import Terminal256Formatter

from dossier_kafka import BMMDossier, startup_dir, XASFile, SEADFile, LSFile, RasterFiles
from gonio import XRRFile

dossier = BMMDossier()
xdi  = XASFile()
sead = SEADFile()
ls   = LSFile()
raster = RasterFiles()
xrr = XRRFile()

# capture Ctrl-c to exit kafka polling loop semi-gracefully
def handler(signal, frame):
    print('Exiting Kafka consumer')
    sys.exit(0)
signal.signal(signal.SIGINT, handler)

import logging
logger = logging.getLogger('BMM file manager logger')
logger.setLevel(logging.INFO)

from file_logger import add_handler, clear_logger, establish_logger
sh = logging.StreamHandler()
add_handler(sh, logger)

be_verbose = True


def pobj(text, style='monokai'):
    '''Pretty print a dictionary representation of an object to the
    screen with syntax highlighting provided by Pygments.

    Default style is easy to read on a dark terminal.
    See https://pygments.org/styles/
    '''
    print(highlight(pprint.pformat(dossier.__dict__),
                    PythonLexer(),
                    Terminal256Formatter(style=style)))


def usb_filename(self, bmm_catalog, uid):
    '''Find the path/name of the asset file associated with UID
    '''
    for d in bmm_catalog[uid].documents():
        if d[0] == 'resource':
            this = os.path.join(d[1]['root'], d[1]['resource_path'])
            if '_%d' in this:
                this = this % 0
            if 'xspress3' in this:
                return this
    return None



def manage_files_from_kafka_messages(beamline_acronym):

    def examine_message(consumer, doctype, doc):
        global be_verbose, dossier, logger
        # print(
        #     f"\n[{datetime.datetime.now().isoformat(timespec='seconds')}] document topic: {doctype}\n"
        #     f"contents: {pprint.pformat(doc)}\n"
        # )
        name, message = doc

        if be_verbose is True:
            print('\n\nVerbose mode is on:')
            pprint.pprint(message)
            print('\n')

        if name == 'bmm':
            # if any(x in message for x in ('dossier', 'mkdir', 'copy', 'touch', 'echoslack',
                #                           'xasxdi', 'seadxdi', 'lsxdi', 'raster', 'next_index')) :
                # if be_verbose is True:
                #     print(f'\n[{datetime.datetime.now().isoformat(timespec="seconds")}]\n{pprint.pformat(message, compact=True)}')
                # else:
                #     print(f'\n[{datetime.datetime.now().isoformat(timespec="seconds")}]')

            if 'dossier' in message:
                if message['dossier'] == 'start':
                    dossier = BMMDossier()
                    logger.info(f'starting dossier for {message["stub"]}')

                elif message['dossier'] == 'set':
                    dossier.set_parameters(**message)
                    # if len(message) > 2:
                    #     logger.info(f'set {len(message)-1} parameters')
                    # else:
                    #     logger.info('set 1 parameter')

                elif message['dossier'] == 'show':
                    #logger.info(pprint.pformat(dossier.__dict__))
                    pobj(dossier)

                elif message['dossier'] == 'motors':
                    try:
                        print(highlight(dossier.motor_sidebar(bmm_catalog),
                                        HtmlLexer(),
                                        Terminal256Formatter(style='monokai')))
                    except Exception as E:
                        logger.error(str(E))

                elif message['dossier'] == 'write':
                    dossier.write_dossier(bmm_catalog, logger)

                elif message['dossier'] == 'sead':
                    dossier.sead_dossier(bmm_catalog, logger)

                elif message['dossier'] == 'raster':
                    dossier.raster_dossier(bmm_catalog, logger)


            elif 'logger' in message:
                if message['logger'] == 'start':
                    establish_logger(logger, folder=message['folder'])
                    logger.info('established file logger')

                elif message['logger'] == 'clear':
                    logger.info('clearing filehandler from logger')
                    clear_logger(logger)

                elif message['logger'] == 'entry':
                    logger.info(message['text'])



            elif 'echoslack' in message:
                if 'img' not in message or message['img'] is None:
                    print(f'sending message "{message["text"]}" to slack')
                    # if 'icon'   not in message: message['icon'] = 'message'
                    if 'rid'    not in message: message['rid']  = None
                    post_to_slack(message['text'], rid = message['rid'])
                    # echo_slack(text   = message['text'],
                    #            icon   = message['icon'],
                    #            rid    = message['rid'] )

                elif 'img' in message and os.path.exists(message['img']):
                    img_to_slack(message['img'])

            elif 'refresh_slack' in message:
                refresh_slack()

            elif 'describe_slack' in message:
                describe_slack()

            elif 'test_slack' in message:
                test_slack()

            elif 'mkdir' in message:
                if os.path.exists(message['mkdir']) is False:
                    os.makedirs(message['mkdir'])
                    logger.info(f'made directory {message["mkdir"]}')

            elif 'copy' in message:
                if 'file' in message:
                    source = message['file']
                elif 'uuid' in message:
                    record = bmm_catalog[message['uuid']]
                    docs = record.documents()
                    found = []
                    for d in docs:
                        if d[0] == 'resource':
                            this = os.path.join(d[1]['root'], d[1]['resource_path'])
                            if '_%d' in this or re.search(r'%\d\.\dd', this) is not None:
                                this = this % 0
                            found.append(this)
                    source = found[0]
                    uuid = True
                target = message['target']
                shutil.copy(source, target)
                logger.info(f'copied {source} to {target}')


            elif 'touch' in message:
                target = message['touch']
                this = open(target, 'a')
                this.close()
                logger.info(f'touched {target}')

            elif 'xasxdi' in message:
                if 'include_yield' in message:
                    include_yield = True
                else:
                    include_yield = False
                xdi.to_xdi(catalog=bmm_catalog, uid=message['uid'], logger=logger, include_yield=include_yield) # , filename=message['filename']

            elif 'everyxas' in message:
                xdi.everyxas(catalog=bmm_catalog, gup=message['gup'], since=message['since'], until=message['until'], logger=logger)

            elif 'seadxdi' in message:
                sead.to_xdi(catalog=bmm_catalog, uid=message['uid'], filename=message['filename'], logger=logger)

            elif 'lsxdi' in message:
                ls.to_xdi(catalog=bmm_catalog, uid=message['uid'], filename=message['filename'], logger=logger)

            elif 'raster' in message:
                raster.preserve_data(catalog=bmm_catalog, uid=message['uid'], logger=logger)

            elif 'next_index' in message:
                next_index(message['folder'], message['stub'])

            elif 'file_exists' in message:
                #pprint.pprint(message)
                file_exists(message['folder'], message['filename'], message['start'], message['stop'], message['number'])


            elif 'xrrout' in message:
                xrr.to_xdi(catalog=bmm_catalog, uid=message['uid'], stub=message['stub'], logger=logger)
                xrr.to_txt(catalog=bmm_catalog, uid=message['uid'], stub=message['stub'], style='both', logger=logger)

            elif 'xrrxdi' in message:
                xrr.to_xdi(catalog=bmm_catalog, uid=message['uid'], stub=message['stub'], logger=logger)

            elif 'xrrtxt' in message:
                xrr.to_txt(catalog=bmm_catalog, uid=message['uid'], stub=message['stub'], style=message['style'], logger=logger)

            elif 'xrr_linescan_file' in message:
                xrr.linescan_file(catalog=bmm_catalog, uid=message['uid'], stub=message['stub'],
                                     motor=message['motor'], detector=message['detector'], logger=logger)
                
            elif 'xrr_calibration_file' in message:
                xrr.calibration_file(catalog=bmm_catalog, uid=message['uid'], stub=message['stub'],
                                     motor=message['motor'], detector=['detector'], logger=logger)

            elif 'mythen_calibration' in message:
                xrr.mythen_calibration(catalog=bmm_catalog,
                                       uid=message['uid'],
                                       path=message['path'],
                                       now=message['now'],
                                       stamp=message['stamp'],
                                       setup=message['setup'],
                                       gap=message['gap'],
                                       energy=message['energy'],
                                       pixel0=message['pixel0'],
                                       angle_per_pixel=message['angle_per_pixel'],
                                       stub=message['stub'],
                                       dw=message['dw'],
                                       rw=message['rw'],
                                       slits_b=message['slits_b'],
                                       slits_i=message['slits_i'],
                                       slits_o=message['slits_o'],
                                       slits_t=message['slits_t'],
                                       logger=logger)
                                       
                                       
                
    kafka_config = nslsii.kafka_utils._read_bluesky_kafka_config_file(config_file_path="/etc/bluesky/kafka.yml")

    # this consumer should not be in a group with other consumers
    #   so generate a unique consumer group id for it
    unique_group_id = f"echo-{beamline_acronym}-{str(uuid.uuid4())[:8]}"
    kafka_consumer = BasicConsumer(
        #topics            = [f"{beamline_acronym}.bluesky.runengine.documents", f"{beamline_acronym}.test"],
        topics            = [f"{beamline_acronym}.test"],
        bootstrap_servers = kafka_config["bootstrap_servers"],
        group_id          = unique_group_id,
        consumer_config   = kafka_config["runengine_producer_config"],
        process_message   = examine_message,
    )

    try:
        kafka_consumer.start_polling(work_during_wait=lambda : time.sleep(0.1))
    except KeyboardInterrupt:
        print('\n\nExiting Kafka consumer (file manager)')
        return()

print('Ready to receive documents...')
manage_files_from_kafka_messages('bmm')
