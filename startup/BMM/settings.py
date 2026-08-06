from BMM.user_ns.base import reload_profile_configuration, profile_configuration, startup_dir
from rich import print as cprint
import json

def settings():
    '''Display settings from BMM_configuration.ini in the terminal.
    '''
    choices, count = [], 0
    print('Select a configuration group:\n')
    for k in profile_configuration.keys():
        if 'description' in k:
            continue
        if 'DEFAULT' in k:
            continue
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
    for i in profile_configuration.items(choices[choice]):
        cprint(f'[dark_olive_green3]{choices[choice]}.[/dark_olive_green3][deep_pink1]{i[0]}[/deep_pink1]')
        d = profile_configuration.get(choices[choice]+'.descriptions', i[0])
        cprint(f'\tdescription: [grey66]{d}[/grey66]')
        cprint(f'\tvalue: [yellow3]{i[1]}[/yellow3]')


def ini2json():
    allconfig = dict()
    for k in profile_configuration.keys():
        if 'description' in k:
            continue
        if 'DEFAULT' in k:
            continue
        allconfig[k] = dict()
        for i in profile_configuration.items(k):
            print(k, i)
            allconfig[k][i[0]] = dict()
            if i[1] == 'True':
                allconfig[k][i[0]]['value'] = True
            elif i[1] == 'False':
                allconfig[k][i[0]]['value'] = False
            elif i[1].isnumeric():
                allconfig[k][i[0]]['value'] = int(i[1])
            else:
                try:
                    allconfig[k][i[0]]['value'] = float(i[1])
                except:
                    allconfig[k][i[0]]['value'] = i[1]

            allconfig[k][i[0]]['description'] = profile_configuration.get(k+".descriptions",i[0])

    filename = os.path.join(startup_dir, "BMM_configuration.json")
    with open(filename, 'w') as outfile:
        json.dump(allconfig, outfile, indent=4)
    return allconfig
            
