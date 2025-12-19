import sys, os
sys.path.append('/home/xf06bm/.ipython/profile_common/')
sys.path.append('/home/xf06bm/.local/lib/python3.11/site-packages/')


from BMM.user_ns import *




# def handler(signum, frame):
#     #print(f'Handling signal {signum} ({signal.Signals(signum).name}).')
#     text = termcolor.colored('''
# You have just hit Ctrl-z!

# This will pause the data acquisition program and return you to the
# Unix command line.

#     *** From the Unix command line, return here ***
#     ***      by entering the "fg" command       ***

# If that is what you want to do, type "y" then hit enter (or simply hit enter).

# If you would like to remain in the data acquisition program, type "n" then enter.

# ''', 'red', 'on_white', attrs=['bold'])
#     PROMPT = f"[{termcolor.colored('yes', attrs=['underline'])}: y then Enter (or just Enter) ● {termcolor.colored('no', attrs=['underline'])}: n then Enter] "
#     action = input(text + 'Do you want to pause? ' + PROMPT)
#     if len(action) == 0 or action.lower() != 'n':
#         os.system('kill -STOP %d'  % os.getpid())


# signal.signal(signal.SIGTSTP, handler)
