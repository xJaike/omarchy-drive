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
        current: preview.demoState === "uploading" ? ({direction: "upload", name: "Documents/Project brief.pdf", bytes: 5242880, total: 10485760, percent: 50, speed: 2097152, eta: 3})
               : preview.demoState === "folder" ? ({direction: "prepare", label: "Create Drive folder", name: "Projects/site/assets", bytes: 0, total: 0, percent: 0, speed: 0, eta: null}) : null
        active: preview.demoState === "uploading" ? ({action: "upload", path: "Documents/Project brief.pdf"})
              : preview.demoState === "folder" ? ({action: "mkdir-remote", path: "Projects/site/assets"}) : null
        checking: preview.demoState === "checking" ? ({side: "local", path: "Projects/site/node_modules/some-package/dist/a-rather-long-file-name.min.js", count: 12480}) : null
        total: preview.demoState === "uploading" || preview.demoState === "folder" ? 120 : 0
        completed: preview.demoState === "uploading" || preview.demoState === "folder" ? 3 : 0
        queued: preview.demoState === "uploading" || preview.demoState === "folder" ? 116 : 0
        queue: preview.demoState === "uploading" || preview.demoState === "folder" ? [
          {action: "upload", path: "Notes/Ideas.md"},
          {action: "download", path: "Photos/Harbor.jpg"},
          {action: "mkdir-remote", path: "Projects/site"},
          {action: "upload", path: "Documents/Budget.xlsx"}
        ] : []
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
