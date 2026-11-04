import QtQuick
import Quickshell
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// The shell's generic tooltip snapshots text on entry. This widget-local
// tooltip keeps a live binding to sync status without reopening the window.
PopupWindow {
  id: root
  required property Item target
  property string text: ""
  property string position: "top"
  property bool opened: false

  visible: opened && target.tooltipHovered && target.visible
  color: "transparent"
  implicitWidth: Math.min(600, screen ? screen.width - 32 : 600)
  implicitHeight: Math.min(label.implicitHeight + 24, screen ? screen.height - 64 : 720)

  anchor {
    item: root.target
    edges: Edges.Top | Edges.Left
    gravity: Edges.Bottom | Edges.Right
    adjustment: PopupAdjustment.Slide
    rect.width: 1
    rect.height: 1
    rect.x: root.position === "left" ? root.target.width + 6
          : root.position === "right" ? -root.implicitWidth - 6
          : root.target.width / 2 - root.implicitWidth / 2
    rect.y: root.position === "bottom" ? -root.implicitHeight - 6
          : root.position === "left" || root.position === "right"
            ? root.target.height / 2 - root.implicitHeight / 2
            : root.target.height + 6
  }

  BorderSurface {
    anchors.fill: parent
    color: Commons.Color.tooltip.background
    borderSpec: Border.surfaceSpec("tooltip", "border", Commons.Color.tooltip.border, 1)
    radius: Style.cornerRadius
    clip: true

    Text {
      id: label
      x: 12
      y: 12
      width: parent.width - 24
      text: root.text
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      color: Commons.Color.tooltip.text
      font.family: Style.font.family
      font.pixelSize: Style.font.body
      horizontalAlignment: Text.AlignLeft
    }
  }
}
