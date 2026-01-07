from BMM.functions import run_report

run_report(__file__, text='plan suspenders')

from BMM.user_ns.metadata import ring
from BMM.user_ns.instruments import bmps, sha, shb
from BMM.user_ns.kafka import kafka
from BMM.user_ns.base import RE

from bmm_tools.tools.suspenders import BMMSuspenders

suspenders = BMMSuspenders(re=RE, kafka=kafka, ring=ring, bmps=bmps, sha=sha, shb=shb)
