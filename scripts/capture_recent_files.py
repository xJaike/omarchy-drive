#!/usr/bin/env python3
"""Capture the real RecentFiles QML component with isolated synthetic data."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def geometry(value):
    if isinstance(value, dict):
        if value.get('namespace') == 'omarchy-drive-docs-preview':
            return ','.join(str(int(value[key])) for key in ('x','y')) + ' ' + 'x'.join(str(int(value[key])) for key in ('w','h'))
        for child in value.values():
            found = geometry(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = geometry(child)
            if found:
                return found
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/images/recent-files.png')
    parser.add_argument('--state', choices=('uploading','attention','empty'), default='uploading')
    parser.add_argument('--palette', choices=('current', 'light', 'dark'), default='current', help='Isolated theme preview; does not change the desktop theme')
    args = parser.parse_args()
    for tool in ('quickshell','hyprctl','grim'):
        if not shutil.which(tool):
            raise SystemExit('Documentation capture requires ' + tool + ' in a running Omarchy desktop.')
    shell = Path('/usr/share/omarchy/shell')
    with tempfile.TemporaryDirectory(prefix='omarchy-drive-docs-') as temporary:
        folder = Path(temporary)
        for name in ('Commons','Ui'):
            shutil.copytree(shell/name,folder/name)
        for name in ('ThemeBridge.qml','RecentFiles.qml','DriveGlyph.qml','GoogleDriveIcon.qml','Model.js'):
            shutil.copy2(ROOT/name,folder/name)
        scene = (ROOT/'scripts/preview_recent_files.qml').read_text().replace('property string demoState: "uploading"', 'property string demoState: "' + args.state + '"')
        if args.palette != 'current':
            palettes = {
                'light': {'foreground': '#202938', 'background': '#f3f5f7', 'accent': '#2457bd', 'urgent': '#b42332', 'muted': '#596779'},
                'dark': {'foreground': '#e3e8f0', 'background': '#151923', 'accent': '#93b4ff', 'urgent': '#ff8c99', 'muted': '#9aa9bf'},
            }
            palette = palettes[args.palette]
            assignments = '; '.join('Commons.Color.' + key + ' = ' + json.dumps(value) for key, value in palette.items())
            assignments += '; Commons.Color.shellValues = ({})'
            scene = scene.replace('ShellRoot {', 'ShellRoot { Timer { interval: 100; running: true; onTriggered: { ' + assignments + ' } }', 1)
        (folder/'shell.qml').write_text(scene)
        with (folder/'render.log').open('w+') as log:
            process = subprocess.Popen(['quickshell','--path',str(folder/'shell.qml')],stdout=log,stderr=subprocess.STDOUT)
            try:
                deadline=time.monotonic()+12
                rect=None
                while time.monotonic()<deadline and process.poll() is None:
                    layers=subprocess.run(['hyprctl','layers','-j'],capture_output=True,text=True,timeout=3)
                    if layers.returncode==0:
                        rect=geometry(json.loads(layers.stdout))
                    if rect:
                        break
                    time.sleep(.2)
                if not rect:
                    log.seek(0)
                    raise SystemExit('Could not render documentation preview:\n'+log.read())
                time.sleep(.5)
                args.output.parent.mkdir(parents=True,exist_ok=True)
                subprocess.run(['grim','-g',rect,str(args.output)],check=True)
                print(args.output)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    main()
