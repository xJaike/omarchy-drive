import QtQuick
import Quickshell
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons

ShellRoot {
  PanelWindow {
    id: preview
    property string demoState: "uploading"
    anchors { top: true; left: true }
    margins { top: 40; left: 40 }
    exclusiveZone: 0
    WlrLayershell.namespace: "omarchy-drive-docs-preview"
    WlrLayershell.layer: WlrLayer.Overlay
    implicitWidth: 440
    implicitHeight: content.implicitHeight + 40
    color: Commons.Color.popups.background

    Column {
      id: content
      x: 20
      y: 20
      width: parent.width - 40
      spacing: 16
      Row {
        spacing: 12
        GoogleDriveIcon { iconSize: 28; color: Commons.Color.popups.text }
        Column {
          spacing: 3
          Text { text: "Google Drive"; color: Commons.Color.popups.text; font.family: Style.font.family; font.pixelSize: 16; font.bold: true }
          Text { text: "RECENT FILES · DEMO"; color: Commons.Color.muted; font.family: Style.font.family; font.pixelSize: 10 }
        }
      }
      RecentFiles {
        width: parent.width
        remaining: 3
        retryEnabled: preview.demoState === "attention"
        current: preview.demoState === "uploading" ? ({direction: "upload", name: "Documents/Project brief.pdf", bytes: 5242880, total: 10485760, percent: 50, speed: 2097152, eta: 3}) : null
        entries: preview.demoState === "empty" ? [] : [
          {path: "Documents/Project brief.pdf", action: "upload", state: "completed", size: 10485760, time: Date.now()/1000-45},
          {path: "Photos/Harbor.jpg", action: "download", state: "failed", error: "Connection interrupted. Retry sync to resume.", time: Date.now()/1000-120},
          {path: "Notes/Ideas.md", action: "download", state: "completed", size: 4096, time: Date.now()/1000-300},
          {path: "Documents/A very long document title with a readable extension.pdf", action: "upload", state: "completed", size: 8192, time: Date.now()/1000-600},
          {path: "Documents/Meeting notes.txt", action: "upload", state: "completed", size: 2048, time: Date.now()/1000-900}
        ]
      }
      Text {
        width: parent.width
        text: "Native Omarchy component · Sample files only\nNo Google account or file content is accessed."
        color: Commons.Color.muted
        textFormat: Text.PlainText
        font.family: Style.font.family
        font.pixelSize: 10
        wrapMode: Text.Wrap
      }
    }
  }
}
