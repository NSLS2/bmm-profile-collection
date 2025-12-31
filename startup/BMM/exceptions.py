
class FailedDCMParaException(Exception):
    '''Raise this Exception when dcm.para fails to arrive in position.'''


class ArrivedInModeException(Exception):
    '''Raise this Exception when change_edge() fails to complete successfully.'''

class ChangeModeException(Exception):
    '''Raise this Exception when something goes wrong when running change_mode().'''
    
