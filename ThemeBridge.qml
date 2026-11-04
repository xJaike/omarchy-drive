import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons as Commons

// Installed plugins can retain their own Commons instance across shell theme
// IPC updates. Follow the active files too, including atomic directory swaps
// which can detach a file watcher from the newly selected theme.
Item {
  id: root
  visible: false
  property string themePath: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme"
  property string lastShell: ""

  FileView {
    id: colors
    path: root.themePath + "/colors.toml"
    preload: true
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      var raw = text()
      if (raw.trim()) {
        Commons.Color.loadColors(raw)
      }
    }
  }
  FileView {
    id: surfaces
    path: root.themePath + "/shell.toml"
    preload: true
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      var raw = text()
      if (raw.trim() && (raw !== root.lastShell || JSON.stringify(Commons.Color.parseShell(raw)) !== JSON.stringify(Commons.Color.themeShellValues))) {
        root.lastShell = raw
        Commons.Color.loadShell(raw)
      }
    }
  }
  Timer {
    interval: 1500
    running: true
    repeat: true
    onTriggered: { colors.reload(); surfaces.reload() }
  }
}
