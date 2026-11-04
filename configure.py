#!/usr/bin/env python3
"""Interactive account setup launched from the panel, never during a status poll."""
from pathlib import Path
import subprocess
import sys
from omarchy_drive.cli import GUIDE, main
from omarchy_drive.storage import Paths, read_json

root = Path(__file__).resolve().parent
if not sys.stdin.isatty():
    print('Run: bash ' + str(root / 'install.sh'))
    print(GUIDE)
    raise SystemExit(0)
try:
    print('Install standalone Drive helpers. This does not start a sync.\n', flush=True)
    result = subprocess.call(['bash', str(root / 'install.sh')])
    if result == 0:
        paths = Paths()
        if read_json(paths.settings).get('folder_id') and paths.tokens.exists() and read_json(paths.status).get('kind') != 'auth':
            print('\nExisting Google connection retained. Open the panel and review your selected folders.')
        else:
            print(GUIDE)
            client = input('Desktop OAuth JSON path (blank to finish later): ').strip()
            if client:
                main(['setup', '--client', str(Path(client).expanduser())])
    input('\nPress Enter to close…')
except (EOFError, KeyboardInterrupt):
    pass
