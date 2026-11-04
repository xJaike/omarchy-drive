#!/usr/bin/env python3
"""Verify live theme updates in a separate QML process; never changes the desktop."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='drive-theme-test-') as temporary:
        folder = Path(temporary)
        shutil.copytree('/usr/share/omarchy/shell/Commons', folder/'Commons')
        shutil.copy2(ROOT/'ThemeBridge.qml', folder/'ThemeBridge.qml')
        theme = folder/'theme'
        theme.mkdir()
        (theme/'colors.toml').write_text('foreground = "#112233"\nbackground = "#eeeeee"\n')
        (theme/'shell.toml').write_text('[popups]\ntext = "#112233"\nbackground = "#eeeeee"\n')
        (folder/'shell.qml').write_text('''import QtQuick
import Quickshell
import qs.Commons as Commons
ShellRoot {
  ThemeBridge { themePath: Qt.resolvedUrl("theme").toString().replace("file://", "") }
  Timer { interval: 200; running: true; repeat: true
    onTriggered: console.log("PALETTE", Commons.Color.popups.text.toString(), Commons.Color.popups.background.toString())
  }
}
''')
        with (folder/'output.log').open('w+') as log:
            process = subprocess.Popen(['quickshell', '--path', str(folder/'shell.qml')], stdout=log, stderr=subprocess.STDOUT)
            try:
                def wait_for(value):
                    deadline = time.monotonic()+8
                    while time.monotonic() < deadline:
                        log.seek(0)
                        output = log.read()
                        if value in output:
                            return
                        if process.poll() is not None:
                            raise AssertionError(output)
                        time.sleep(.1)
                    raise AssertionError('Palette did not update: '+output)
                wait_for('PALETTE #112233 #eeeeee')
                replacement = folder/'replacement'
                replacement.mkdir()
                (replacement/'colors.toml').write_text('foreground = "#ddeeff"\nbackground = "#111122"\n')
                (replacement/'shell.toml').write_text('[popups]\ntext = "#ddeeff"\nbackground = "#111122"\n')
                theme.rename(folder/'old-theme')
                replacement.rename(theme)
                wait_for('PALETTE #ddeeff #111122')
                print('PASS: running QML palette updated after atomic theme-directory replacement')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    main()
