from BMM.functions import run_report
from rich import print as cprint
import time

run_report(__file__, text='plan suspenders')

from BMM.user_ns.instruments import bmps, sha, shb
from BMM.user_ns.kafka import kafka
from BMM.user_ns.base import RE

from bmm_tools.devices.ring import Ring
from bmm_tools.tools.suspenders import BMMSuspenders

ring = Ring('SR', name='ring')

base_sleep = 0.5
max_count = 4
count = 0

## need to wait for the ring object to be connected before making suspenders
while count < max_count:
    if ring.filltarget.connected is True:
        break
    time.sleep(base_sleep*2**count)
    count += 1

suspenders = BMMSuspenders(re=RE, kafka=kafka, ring=ring, bmps=bmps, sha=sha, shb=shb)
if suspenders.errors != '':
    cprint(f'[orange_red1]{suspenders.errors}[/orange_red1]')
