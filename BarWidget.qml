import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.xjaike.omarchy-drive"
  property var status: ({kind: "waiting", label: "Drive …", tooltip: "Loading sync status"})
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function refresh() {
    if (!poll.running) poll.running = true
  }

  Process {
    id: poll
    command: ["python3", Qt.resolvedUrl("status.py").toString().replace(/^file:\/\//, "")]
    stdout: StdioCollector {
      onStreamFinished: {
        try { root.status = JSON.parse(this.text) }
        catch (e) { root.status = {kind: "error", label: "Drive ?", tooltip: "Could not read sync status"} }
      }
    }
  }

  Timer {
    interval: 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.vertical ? "D" : root.status.label
    tooltipText: root.status.tooltip
    active: root.status.kind === "error"
    dimmed: root.status.kind === "paused" || root.status.kind === "setup"
    fontSize: Style.font.caption
    onPressed: function(b) {
      if (b === Qt.LeftButton)
        Quickshell.execDetached(["omarchy-launch-terminal", "python3", Qt.resolvedUrl("progress.py").toString().replace(/^file:\/\//, "")])
      else if (b === Qt.MiddleButton)
        Quickshell.execDetached(["systemctl", "--user", "start", "--no-block", "omarchy-drive-sync.service"])
      else if (b === Qt.RightButton)
        Quickshell.execDetached(["xdg-open", Quickshell.env("HOME") + "/GoogleDrive"])
      root.refresh()
    }
  }
}
