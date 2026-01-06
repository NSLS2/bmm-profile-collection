
# note that this file will never be imported when using queueserver
# so the explicit calls to IPython functionality should be OK

#from bmm_tools.tools.messages import *  # error_msg et al. + boxedtext

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)

from rich import print as cprint

from IPython.terminal.prompts import Prompts, Token
class BMMPrompt(Prompts):
    def in_prompt_tokens(self, cli=None):
        BMMuser, dcm = user_ns['BMMuser'], user_ns['dcm']
        # if idps.state.get() == 0:
        #     shatoken = (Token.OutPromptNum, ' A')
        # else:
        #     shatoken = (Token.Tilde, ' A')

        # if shb.state.get() == 1:
        #     shbtoken = (Token.OutPromptNum, 'B')
        # else:
        #     shbtoken = (Token.Tilde, 'B')

        synstoken = (Token.Text, '')
        if BMMuser.syns is True:
            synstoken = (Token.OutPromptNum, '!!! ')
            
        if BMMuser.user_is_defined:
            bmmtoken = (Token.Prompt, 'BMM ')
        else:
            bmmtoken = (Token.OutPrompt, 'BMM ')
        ## BMM XRD.311 A•B 0.0 [5] ▶
        # rcv = None
        # try:
        #     rcv = ring.current.get()
        # except:
        #     rcv = None
        # if rcv is None:
        #     rcv = 0
        return [synstoken,
                bmmtoken,
                (Token.CursorLine, '%s.%s' % (BMMuser.pds_mode, dcm._crystal)),
                #shatoken,
                #(Token.Prompt, u'\u2022'),
                #shbtoken,
                #(Token.Comment, ' %.1f ' % rcv),
                (Token.Prompt, ' ['),
                (Token.PromptNum, str(self.shell.execution_count)),
                (Token.Prompt, '] ' + u"\u25B6" + ' ')]


# def explore_tokens(i):
#     tokens = ('Aborted', 'AutoSuggestion', 'ColorColumn', 'Comment',
#               'CursorColumn', 'CursorLine', 'Digraph', 'Error',
#               'Escape', 'Generic', 'Keyword', 'LeadingWhiteSpace',
#               'LineNumber', 'Literal', 'MatchingBracket', 'Menu',
#               'MultipleCursors', 'Name', 'Number', 'Operator',
#               'Other', 'OutPrompt', 'OutPromptNum', 'Prompt',
#               'PromptNum', 'Punctuation', 'Scrollbar', 'SearchMatch',
#               'SelectedText', 'SetCursorPosition', 'String', 'Tab',
#               'Text', 'Tilde', 'Token', 'Toolbar',
#               'TrailingWhiteSpace', 'Transparent', 'WindowTooSmall',
#               'ZeroWidthEscape')
#     class MyPrompt(Prompts):
#         def in_prompt_tokens(self, cli=None):
#             return [(getattr(Token, tokens[i]), 'BMM >'),]
#     ip = get_ipython()
#     ip.prompts = MyPrompt(ip)
#     return 'Token.%s' % tokens[i]


## from Tom on Gitter: https://gitter.im/NSLS-II/DAMA?at=5cdf02ab7c363c75a7f983e1
import types
get_ipython().display_formatter.formatters['text/plain'].for_type(types.GeneratorType, lambda x, y, z: print(f'{x}   Hint: enclose bsui commands in RE()'))




def BMM_help():
    '''
    Print a concise summary of data acquisition commands.
    '''
    BMMuser = user_ns['BMMuser']
    print('')
    cprint('[honeydew2]Open the shutter:[/honeydew2]\t\t[white]shb.open()[/white]')
    cprint('[honeydew2]Close the shutter:[/honeydew2]\t\t[white]shb.close()[/white]')
    print('')
    cprint('[honeydew2]Change edge:[/honeydew2]\t\t\t[white]RE(change_edge("Xx"))[/white]')
    cprint('[honeydew2]Change energy:[/honeydew2]\t\t\t[white]RE(mv(dcm.energy, <energy>))[/white]')
    cprint('[honeydew2]Move a motor, absolute:[/honeydew2]\t\t[white]RE(mv(<motor>, <position>))[/white]')
    cprint('[honeydew2]Move a motor, relative:[/honeydew2]\t\t[white]RE(mvr(<motor>, <delta>))[/white]')
    cprint('[honeydew2]Where is a motor?\t\t[white]%w <motor>[/white]')
    print('')
    cprint('[honeydew2]Where is the DCM?\t\t[white]%w dcm[/white]')
    cprint('[honeydew2]Where is M2?\t\t\t[white]%w m2[/white]')
    cprint('[honeydew2]Where is M3?\t\t\t[white]%w m3[/white]')
    cprint('[honeydew2]Where are the slits?\t\t[white]%w slits3[/white]')
    cprint('[honeydew2]Where is the XAFS table?\t[white]%w xafs_table[/white]')
    print('')
    cprint('[honeydew2]Summarize all motor positions:[/honeydew2]\t[white]%m[/white]')
    cprint('[honeydew2]Summarize utilities:[/honeydew2]\t\t[white]%ut[/white]')
    print('')
    cprint('[honeydew2]How long will a scan seq. be?\t[white]howlong(\'scan.ini\')[/white]')
    cprint('[honeydew2]Run a scan sequence:[/honeydew2]\t\t[white]RE(xafs(\'scan.ini\'))[/white]')
    cprint('[honeydew2]Scan a motor, plot a detector:[/honeydew2]\t[white]RE(linescan(<det>, <motor>, <start>, <stop>, <nsteps>))[/white]')
    cprint('[honeydew2]Scan 2 motors, plot a detector:[/honeydew2]\t[white]RE(areascan(<det>, <slow motor>, <start>, <stop>, <nsteps>, <fast motor>, <start>, <stop>, <nsteps>))[/white]')
    cprint('')
    cprint('[honeydew2]hephaestus:[/honeydew2]\t\t\t[white]%hephaestus[/white]')
    print('')
    cprint('[honeydew2]All the details:[/honeydew2] [underline]https://nsls2.github.io/bmm-beamline-manual/[/underline]')
    return None

def BMM_keys():
    '''
    Print a concise summary of command line hotkeys.
    '''
    print('')
    cprint('[honeydew2]Abort scan:[/honeydew2]\t\t[red on white]Ctrl-c twice![/red on white]')
    cprint('[honeydew2]Search backwards:[/honeydew2]\tCtrl-r')
    cprint('[honeydew2]Quit search:[/honeydew2]\t\tCtrl-g')
    cprint('[honeydew2]Beginning of line:[/honeydew2]\tCtrl-a')
    cprint('[honeydew2]End of line:[/honeydew2]\t\tCtrl-e')
    cprint('[honeydew2]Delete character:[/honeydew2]\tCtrl-d')
    cprint('[honeydew2]Cut text to eol:[/honeydew2]\tCtrl-k')
    cprint('[honeydew2]Cut text to bol:[/honeydew2]\tCtrl-u')
    cprint('[honeydew2]Paste text:[/honeydew2]\t\tCtrl-y')
    cprint('[honeydew2]Clear screen:[/honeydew2]\t\tCtrl-l')
    print('')
    cprint('[honeydew2]More details:[/honeydew2] [underline]https://jakevdp.github.io/PythonDataScienceHandbook/01.02-shell-keyboard-shortcuts.html[/underline]')
    return None
