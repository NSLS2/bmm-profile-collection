import datetime, signal, pprint, uuid, sys, os, time
sys.path.append('/home/xf06bm/.ipython/bmm_tools/src/')
sys.path.append('/home/xf06bm/.ipython/profile_collection/startup')

#from bluesky_kafka import RemoteDispatcher
from bluesky_kafka.consume import BasicConsumer
import nslsii
import nslsii.kafka_utils

from tiled.client import from_profile
bmm_catalog = from_profile('bmm')

import matplotlib.pyplot as plt
import bmm_plot

import matplotlib               # trust environment to set backend correctly

# capture Ctrl-c to exit kafka polling loop semi-gracefully
def handler(signal, frame):
    print('Exiting Kafka consumer')
    sys.exit(0)
signal.signal(signal.SIGINT, handler)

import logging
logger = logging.getLogger('BMM plot manager logger')
logger.setLevel(logging.INFO)

from file_logger import add_handler, clear_logger, establish_logger
sh = logging.StreamHandler()
add_handler(sh, logger)



from xafs_sequence import XAFSSequence
xafsseq = XAFSSequence()
xafsseq.catalog = bmm_catalog
xafsseq.logger = logger

from glancing_angle_stage import GlancingAngle
ga = GlancingAngle()
ga.logger = logger

from align_wheel import AlignWheel
aw = AlignWheel()
aw.logger = logger

be_verbose = False
doing = None

from bmm_live import LineScan, XAFSScan, XRF, AreaScan, XRR
ls  = LineScan()
ls.logger = logger
xs  = XAFSScan()
xs.logger = logger
ts  = LineScan()
ts.logger = logger
xrf = XRF()
xrf.logger = logger
asc = AreaScan()
asc.logger = logger

xrr = XRR()
xrr.logger = logger

from tools import peakfit, rectanglefit, stepfit

if os.getenv('USER') == 'workflow-bmm':
    matplotlib.use('Agg')


# these two lines allow a stale plot to remain interactive and prevent
# the current plot from stealing focus.  thanks to Tom:
# https://nsls2.slack.com/archives/C02D9V72QH1/p1674589090772499
plt.ion()
plt.rcParams["figure.raise_window"] = False

from slack import refresh_slack, describe_slack, test_slack

def plot_from_kafka_messages(beamline_acronym):

    def examine_message(consumer, doctype, doc):
        global doing, be_verbose
        # print(
        #     f"\n[{datetime.datetime.now().isoformat(timespec='seconds')}] document topic: {doctype}\n"
        #     f"contents: {pprint.pformat(doc)}\n"
        # )
        name, message = doc
        # print('========', name)
        # if be_verbose is True:
        #     print('\n\nVerbose mode is on:')
        #     pprint.pprint(message)
        #     print('\n')

        if name == 'bmm':
            if any(x in message for x in ('xafs_sequence', 'glancing_angle', 'align_wheel', 'wafer', 'mono_calibration',
                                          'xrfat', 'linescan', 'xafsscan', 'timescan', 'xrf', 'areascan', 'close', 'logger', 'refresh_slack',
                                          'peakfit', 'stepfit', 'rectanglefit', 'reset_rois',
                                          'backend', 'xrr', 'xrr_alignment', 'xrr_calibration_plot')) :
                if be_verbose is True:
                    print(f'\n[{datetime.datetime.now().isoformat(timespec="seconds")}]\n{pprint.pformat(message, compact=True)}')
                else:
                    print(f'\n[{datetime.datetime.now().isoformat(timespec="seconds")}]') # \ndossier : {message["dossier"]}')

            if 'xafs_sequence' in message:
                if message['xafs_sequence'] == 'start':
                    xafsseq.start(element=message['element'], edge=message['edge'], folder=message['folder'],
                                  workspace=message['workspace'],
                                  repetitions=message['repetitions'], mode=message['mode'])
                elif message['xafs_sequence'] == 'stop':
                    xafsseq.stop(filename=message['filename'])
                elif message['xafs_sequence'] == 'add':
                    xafsseq.add(message['uid'])

            # elif 'xafs_visualization' in message:
            #     xafs_visualization.gridded_plot(uid=message['xafs_visualization'], element=message['element'],
            #                                     edge=message['edge'], folder=message['folder'],
            #                                     mode=message['mode'], catalog=bmm_catalog)
                    
            elif 'glancing_angle' in message:
                if message['glancing_angle'] == 'linear':
                    ga.plot_linear(**message)
                elif message['glancing_angle'] == 'pitch':
                    ga.plot_pitch(**message)
                elif message['glancing_angle'] == 'fluo':
                    ga.plot_fluo(**message)
                elif message['glancing_angle'] == 'start':
                    ga.start(**message)
                elif message['glancing_angle'] == 'stop':
                    ga.stop()

            elif 'align_wheel' in message:
                if message['align_wheel'] == 'start':
                    aw.start(**message)
                elif message['align_wheel'] == 'stop':
                    aw.stop()
                else:
                    aw.plot_rectangle(**message)

            elif 'wafer' in message:
                bmm_plot.wafer_plot(**message)

            elif 'mono_calibration' in message:
                bmm_plot.mono_calibration_plot(**message)

            elif 'xrfat' in message:
                bmm_plot.xrfat(catalog=bmm_catalog, **message)

            elif 'linescan' in message:
                if message['linescan'] == 'start':
                    ls.start(**message)
                    doing = 'linescan'
                elif message['linescan'] == 'stop':
                    ls.stop(catalog=bmm_catalog, **message)
                    doing = None

            elif 'xafsscan' in message:
                if message['xafsscan'] == 'start':
                    xs.start(**message)
                    doing = 'xafsscan'
                elif message['xafsscan'] == 'next':
                    xs.Next(**message)
                elif message['xafsscan'] == 'stop':
                    xs.stop(catalog=bmm_catalog, **message)
                    doing = None

            elif 'timescan' in message:
                if message['timescan'] == 'start':
                    ts.motor = None
                    ts.start(**message)
                    doing = 'timescan'
                elif message['timescan'] == 'stop':
                    ts.stop(catalog=bmm_catalog, **message)
                    doing = None

            elif 'areascan' in message:
                if message['areascan'] == 'start':
                    asc.motor = None
                    asc.start(**message)
                    doing = 'areascan'
                elif message['areascan'] == 'stop':
                    asc.stop(catalog=bmm_catalog, **message)
                    bmm_plot.plot_areascan(bmm_catalog, message['uid'])
                    doing = None

            elif 'xrf' in message:
                if message['xrf'] == 'plot':
                    xrf.plot(catalog=bmm_catalog, **message)
                elif message['xrf'] == 'write':
                    xrf.to_xdi(catalog=bmm_catalog, uid=message['uid'], filename=message['filename'])
                    
            elif 'xrr' in message:
                if message['xrr'] == 'start':
                    xrr.start(**message)
                    doing = 'xrr'
                elif message['xrr'] == 'stop':
                    xrr.stop(catalog=bmm_catalog, **message)
                    doing = None

            elif 'xrr_alignment' in message:
                delta=False
                if 'delta' in message: delta = message['delta']
                xrr.alignment(catalog=bmm_catalog, uid=message['uid'], motor=message['motor'], detector=message['detector'], delta=delta)

            elif 'xrr_calibration_plot' in message:
                xrr.calibration_plot(catalog=bmm_catalog, uid=message['uid'], motor=message['motor'],
                                     detector=message['detector'], stub=message['stub'])

                    
            elif 'reset_rois' in message:
                xrf.reset_rois()
                
            elif 'peakfit' in message:
                if 'spinner' in message:
                    spinner = message['spinner']
                else:
                    spinner = None
                peakfit(catalog = bmm_catalog,
                        uid     = message['uid'],
                        motor   = message['motor_name'],
                        signal  = message['signal'],
                        choice  = message['choice'],
                        spinner = spinner,
                        ga      = ga)
                    
            elif 'rectanglefit' in message:
                if 'drop' in message:
                    drop = message['drop']
                else:
                    drop = None
                rectanglefit(catalog = bmm_catalog,
                             uid     = message['uid'],
                             motor   = message['motor_name'],
                             signal  = message['signal'],
                             drop    = drop,
                             aw      = aw)

            elif 'stepfit' in message:
                if 'spinner' in message:
                    spinner = message['spinner']
                else:
                    spinner = None
                stepfit(catalog = bmm_catalog,
                        uid     = message['uid'],
                        motor   = message['motor_name'],
                        signal  = message['signal'],
                        spinner = spinner,
                        ga      = ga)


                    
            elif 'verbose' in message:
                be_verbose = message['verbose']
            
            elif 'close' in message:
                if message['close'] == 'all':
                    plt.close('all')
                elif message['close'] == 'line':
                    ls.close_all_lineplots()
                elif message['close'] == 'last':
                    plt.close(ls.plots[-1])


            elif 'logger' in message:
                if message['logger'] == 'start':
                    establish_logger(logger, folder=message['folder'])
                    #logger.info('established dossier logger')

                elif message['logger'] == 'clear':
                    #logger.info('clearing filehandler from logger')
                    clear_logger(logger)

                #elif message['logger'] == 'entry':
                #    logger.info(message['text'])

            elif 'refresh_slack' in message:
                refresh_slack()
                    
            elif 'describe_slack' in message:
                describe_slack()

            elif 'test_slack' in message:
                test_slack()

            elif 'backend' in message:
                print(matplotlib.get_backend())
                

                    
            # elif 'resting_state' in message:
            #     if doing == 'timescan':
            #         ts.stop()
            #     elif doing == 'xafsscan':
            #         xs.stop()
            #     elif doing == 'linescan':
            #         ts.stop()
            #     #elif doing == 'areascan':
            #     doing = None

        # for live plotting, need to capture and parse event
        # documents. use the global state variable "doing"
        # to keep track of which plotting chore needs to be done.
        elif 'event' in name:
            if name == 'event_page':
                message = flatten_message(message)
            if doing is None:
                pass
            elif doing == 'linescan':
                ##pprint.pprint(message)
                ls.add(**message)
            elif doing == 'xafsscan':
                #pprint.pprint(message)
                xs.add(**message)
            elif doing == 'timescan':
                #pprint.pprint(message)
                ts.add(**message)
            elif doing == 'areascan':
                #pprint.pprint(message)
                asc.add(**message)
            elif doing == 'xrr':
                #pprint.pprint(message)
                xrr.add(**message)
            else:
                pass
                
        if name == 'stop':
            #print(
            #    f"{datetime.datetime.now().isoformat()} document: {name}\n"
            #    f"contents: {pprint.pformat(doc)}\n"
            #)
            #return
            uid = message['run_start']  # stop document is the second item in the doc list
            record = bmm_catalog[uid]
            verbose = False
            if 'BMM_kafka' in record.metadata['start']:
                hint = record.metadata['start']['BMM_kafka']['hint']
                #print(f'[{datetime.datetime.now().isoformat(timespec="seconds")}]   {uid}')
                # for k in record.metadata['start']['BMM_kafka'].keys():
                #     if k == 'hint':
                #         continue
                #     print(f"\t\t{k}: {record.metadata['start']['BMM_kafka'][k]}")

        #        if hint.startswith('areascan'):
        #            if verbose: print('saw a areascan stop doc')
        #            print(f"{datetime.datetime.now().isoformat()} areascan stop document: {name}\n")
        #            bmm_plot.plot_areascan(bmm_catalog, uid)
        #         elif hint.startswith('linescan'):
        #             if verbose: print('saw a linescan stop doc')
        #             #bmm_plot.plot_linescan(bmm_catalog, uid)
        #         elif hint.startswith('timescan'):
        #             if verbose: print('saw a timescan stop doc')
        #             #bmm_plot.plot_timescan(bmm_catalog, uid)
        #         elif hint.startswith('rectanglescan'):
        #             if verbose: print('saw a rectanglescan stop doc')
        #             #bmm_plot.plot_rectanglescan(bmm_catalog, uid)
        #         elif hint.startswith('xafs'):
        #             if verbose: print('saw an xafs stop doc')
        #             #plt.close('all')
        #             #bmm_plot.plot_xafs(bmm_catalog, uid)
    ## end of examine_message ##################################################################
    
    kafka_config = nslsii.kafka_utils._read_bluesky_kafka_config_file(config_file_path="/etc/bluesky/kafka.yml")

    # this consumer should not be in a group with other consumers
    #   so generate a unique consumer group id for it
    unique_group_id = f"echo-{beamline_acronym}-{str(uuid.uuid4())[:8]}"

    kafka_consumer = BasicConsumer(
        topics            = [f"{beamline_acronym}.bluesky.runengine.documents", f"{beamline_acronym}.test"],
        bootstrap_servers = kafka_config["bootstrap_servers"],
        group_id          = unique_group_id,
        consumer_config   = kafka_config["runengine_producer_config"],
        process_message   = examine_message,
    )

    try:
        kafka_consumer.start_polling(work_during_wait=lambda : plt.pause(.1))
    except KeyboardInterrupt:
        print('\n\nExiting Kafka consumer (plotting tool)')
        return()

print('Ready to receive documents...')
plot_from_kafka_messages('bmm')
