from IPython.core.magic import register_line_magic  #, register_cell_magic, register_line_cell_magic)
from BMM.functions import run_report
from BMM.user_ns.bmm import BMMuser
from os import getenv

run_report(__file__, text='ipython magics for BMM ... general help: %h')

from BMM.prompt import BMM_help, BMM_keys
@register_line_magic
def h(line):
    '''BMM help text'''
    BMM_help()
    return None

@register_line_magic
def k(line):
    '''help on ipython keyboard shortcuts'''
    BMM_keys()
    return None

from BMM.user_ns.utilities import su, show_vacuum, sw
@register_line_magic
def ut(line):
    '''show BMM utilities status'''
    su()
    return None

@register_line_magic
def v(line):
    '''show BMM vacuum status'''
    show_vacuum()
    return None

from BMM.edge import show_edges
@register_line_magic
def se(line):
    '''show foils and ROIs cnfiguration'''
    show_edges()
    return None

@register_line_magic
def h2o(line):
    '''show BMM DI and PCW water status'''
    sw()
    return None

from BMM.motor_status import ms
@register_line_magic
def m(line):
    '''show BMM motor status'''
    ms()
    return None

@register_line_magic
def xm(line):
    '''show XRD motor status'''
    xrdm()
    return None

from BMM.user_ns.motors import *
from BMM.user_ns.instruments import *
from BMM.user_ns.dcm import dcm
@register_line_magic
def w(arg):
    '''show a motor position'''
    words = {'detector': 'det',
             'slits' : 'slits3',
             }
    if arg in words:
        arg = words[arg]
    try:
        motor = eval(arg)
        return motor.wh()
    except:
        print(f'{arg} is not a thing that can be probed for position')

@register_line_magic
def sam(arg):
    print(f'xafs_samx = {xafs_refx.position}   xafs_samy = {xafs_refy.position}')
    return

        
from BMM.user_ns.bmm import kafka
@register_line_magic
def ca(arg):
    '''close all plots'''
    kafka.message({'close': 'all'})
    return None

from BMM.user_ns.detectors import xs4, xs1, xs7, xs
@register_line_magic
def xrf4(arg):
    xs4.measure_xrf()
    return None

@register_line_magic
def xrf1(arg):
    xs1.measure_xrf()
    return None

@register_line_magic
def xrf7(arg):
    xs7.measure_xrf()
    return None

@register_line_magic
def xrf(arg):
    if xs is xs4:
        xs4.measure_xrf()
        return None
    elif xs is xs1:
        xs1.measure_xrf()
        return None
    elif xs is xs7:
        xs7.measure_xrf()
        return None

@register_line_magic
def condaenv(arg):
    print(getenv('BS_ENV'))
    return None
    
from BMM.demeter import run_hephaestus
# @register_line_magic
# def athena(arg):
#     run_athena()
#     return None
@register_line_magic
def hephaestus(arg):
    run_hephaestus()
    return None

