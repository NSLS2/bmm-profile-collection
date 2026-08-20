from BMM.user_ns.base import reload_profile_configuration, profile_configuration, startup_dir
from rich import print as cprint
import json

def settings():
    '''Display settings from BMM_configuration.ini in the terminal.
    '''
    choices, count = [], 0
    print('Select a configuration group:\n')
    for k in profile_configuration.keys():
        print(f'  {count+1}. {k}')
        choices.append(k)
        count += 1
    print('\n  r: return')

    choice = input("\nSelect a group > ")
    try :
        choice = int(choice)
        choice = choice-1
    except:
        return
    if choice <0 or choice >= len(choices):
        return

    print()

    for k in profile_configuration[choices[choice]]:
        if 'description' in k:
            continue
        cprint(f'[dark_olive_green3]{choices[choice]}.[/dark_olive_green3][deep_pink1]{k}[/deep_pink1]')
        d = profile_configuration[choices[choice]][k+'_description']
        v = profile_configuration[choices[choice]][k]
        cprint(f'\tdescription: [grey66]{d}[/grey66]')
        cprint(f'\tvalue: [yellow3]{v}[/yellow3]')


            
