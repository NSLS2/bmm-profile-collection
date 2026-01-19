from BMM.functions import run_report

run_report(__file__, text='plan suspenders')

from BMM.user_ns.instruments import bmps, sha, shb
from BMM.user_ns.kafka import kafka
from BMM.user_ns.base import RE

from bmm_tools.devices.ring import Ring
from bmm_tools.tools.suspenders import BMMSuspenders

ring = Ring('SR', name='ring')

suspenders = BMMSuspenders(re=RE, kafka=kafka, ring=ring, bmps=bmps, sha=sha, shb=shb)
