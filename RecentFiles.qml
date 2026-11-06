import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

Column {
  id: root
  property var current: null
  property var checking: null
  property var active: null
  property var queue: []
  property int total: 0
  property int completed: 0
  property int queued: 0
  readonly property var waiting: Model.waitingRows(queue, 3)
  property var entries: []
  property int remaining: 0
  property bool retryEnabled: false
  property bool expanded: false
  property color foreground: Commons.Color.popups.text
  property color urgent: Commons.Color.urgent
  property string fontFamily: Style.font.family
  property double nowMs: Date.now()
  readonly property color dim: Commons.Color.muted
  readonly property var rows: Model.recentFileRows(entries)
  readonly property var visibleRows: expanded ? rows : rows.slice(0, 4)
  signal revealRequested(string path)
  signal retryRequested()
  signal interaction()
  spacing: 12
  component Label: Text {
    color: root.foreground
    font.family: root.fontFamily
    font.pixelSize: 12
    textFormat: Text.PlainText
  }
  Timer { interval: 30000; running: root.rows.length > 0; repeat: true; onTriggered: root.nowMs = Date.now() }
  Column {
    visible: root.checking !== null && root.current === null
    width: parent.width
    spacing: 4
    Label { text: Model.checkingHeading(root.checking); font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1; opacity: 0.8; width: parent.width; elide: Text.ElideRight }
    Label { text: Model.cleanText(root.checking ? root.checking.path : ""); font.pixelSize: 10; opacity: 0.6; width: parent.width; elide: Text.ElideMiddle }
  }
  Column {
    visible: root.current !== null
    width: parent.width
    spacing: 8
    Label { text: Model.syncHeading(root.total, root.completed); font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1; opacity: 0.8 }
    Rectangle {
      id: transfer
      width: parent.width
      height: transferBody.implicitHeight + 16
      radius: 4
      color: Util.alpha(root.foreground, 0.04)
      border.color: Util.alpha(root.foreground, 0.18)
      readonly property var transferData: root.current || ({})
      readonly property real fraction: Math.max(0,Math.min(1,Number(transferData.percent || 0)/100))
      readonly property bool measured: Model.hasBytes(root.current)
      Column {
        id: transferBody
        x: 8; y: 8; width: parent.width - 16; spacing: 8
        RowLayout {
          width: parent.width; spacing: 8
          DriveGlyph { name: Model.activeGlyph(root.active, root.current); color: root.foreground; Layout.preferredWidth: 18; Layout.preferredHeight: 18 }
          ColumnLayout {
            Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 2
            Label { text: Model.fileName(transfer.transferData.name); font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
            Label { text: (transfer.measured ? "" : Model.cleanText(transfer.transferData.label) + " · ") + Model.parentPath(transfer.transferData.name); font.pixelSize: 10; opacity: 0.6; Layout.fillWidth: true; elide: Text.ElideRight }
          }
          Column {
            visible: transfer.measured
            spacing: 2
            Label { text: Number(transfer.transferData.speed || 0) > 0 ? Model.formatBytes(transfer.transferData.speed) + "/s" : "Estimating…"; font.pixelSize: 10 }
            Label { text: transfer.transferData.eta !== null && transfer.transferData.eta !== undefined ? Model.transferEta(transfer.transferData.eta) + " left" : ""; font.pixelSize: 9; opacity: 0.4; anchors.right: parent.right }
          }
        }
        Rectangle {
          visible: transfer.measured
          width: parent.width; height: 4; radius: 2; color: Util.alpha(root.foreground, 0.18)
          Rectangle { width: parent.width * transfer.fraction; height: 4; radius: 2; color: Commons.Color.accent }
        }
        RowLayout {
          visible: transfer.measured
          width: parent.width
          Label { text: Model.formatBytes(transfer.transferData.bytes) + " / " + Model.formatBytes(transfer.transferData.total); font.pixelSize: 9; opacity: 0.6; Layout.fillWidth: true }
          Label { text: Math.round(transfer.fraction*100) + "%"; font.pixelSize: 9; opacity: 0.6 }
        }
      }
    }
    Column {
      visible: root.waiting.length > 0
      width: parent.width
      spacing: 2
      Repeater {
        model: root.waiting
        RowLayout {
          required property var modelData
          width: parent.width; height: 24; spacing: 12
          Label { text: modelData.glyph; color: root.dim; Layout.preferredWidth: 14; horizontalAlignment: Text.AlignHCenter }
          Label { text: modelData.name; Layout.fillWidth: true; Layout.minimumWidth: 0; elide: Text.ElideRight }
          Label { text: "Waiting"; font.pixelSize: 10; opacity: 0.4 }
        }
      }
      Label { visible: text !== ""; text: Model.waitingMore(root.queued, root.waiting.length); font.pixelSize: 10; opacity: 0.4; leftPadding: 26 }
    }
  }
  Column {
    width: parent.width
    spacing: 8
    RowLayout {
      width: parent.width
      Label { text: "RECENT FILES (" + root.rows.length + ")"; font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1; opacity: 0.8; Layout.fillWidth: true }
      Label {
        text: root.expanded ? "SHOW LESS" : "SHOW HISTORY"
        font.pixelSize: 9; opacity: historyMouse.containsMouse || activeFocus ? 0.9 : 0.4
        visible: root.rows.length > 4
        activeFocusOnTab: true
        Keys.onReturnPressed: root.expanded = !root.expanded
        MouseArea { id: historyMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.expanded = !root.expanded }
      }
    }
    Label { visible: root.rows.length === 0; text: "No recent files yet.\nFiles appear here after a transfer."; font.pixelSize: 10; color: root.dim; width: parent.width; wrapMode: Text.Wrap; topPadding: 6; bottomPadding: 6 }
    Column {
      width: parent.width
      spacing: 2
      Repeater {
        model: root.visibleRows
        Rectangle {
          id: row
          required property var modelData
          width: parent.width; height: 42; radius: 4
          color: modelData.failed ? Util.alpha(root.urgent, 0.05) : mouse.containsMouse || activeFocus ? Util.alpha(root.foreground, 0.08) : "transparent"
          activeFocusOnTab: true
          Accessible.role: Accessible.Button
          Accessible.name: modelData.label + ": " + modelData.name + ". Show containing folder."
          Keys.onReturnPressed: root.revealRequested(modelData.path)
          Keys.onSpacePressed: root.revealRequested(modelData.path)
          MouseArea {
            id: mouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
            onEntered: root.interaction()
            onClicked: { row.forceActiveFocus(); root.revealRequested(row.modelData.path) }
          }
          RowLayout {
            anchors.fill: parent; anchors.margins: 6; spacing: 12
            DriveGlyph { name: row.modelData.failed ? "circle-alert" : row.modelData.action === "download" ? "arrow-down" : "arrow-up"; color: row.modelData.failed ? root.urgent : root.dim; Layout.preferredWidth: 14; Layout.preferredHeight: 14 }
            ColumnLayout {
              Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 2
              Label { text: row.modelData.name; Layout.fillWidth: true; elide: Text.ElideRight }
              Label { text: Model.fileName(row.modelData.parent) + " · " + (row.modelData.failed ? "Failed" : row.modelData.label); font.pixelSize: 10; opacity: row.modelData.failed ? 0.8 : 0.4; color: row.modelData.failed ? root.urgent : root.foreground; Layout.fillWidth: true; elide: Text.ElideRight }
            }
            Column {
              Layout.alignment: Qt.AlignRight | Qt.AlignVCenter; spacing: 3
              Label { text: Model.relativeTime(row.modelData.time,root.nowMs); font.pixelSize: 10; opacity: 0.4; anchors.right: parent.right }
              width: Math.max(timeWidth.implicitWidth,14)
              Label { id: timeWidth; visible: false; text: Model.relativeTime(row.modelData.time,root.nowMs); font.pixelSize: 10 }
              DriveGlyph {
                anchors.right: parent.right
                name: row.modelData.failed ? "rotate-cw" : "folder-open"
                visible: row.modelData.failed || mouse.containsMouse || row.activeFocus
                color: row.modelData.failed ? root.urgent : root.dim
                width: 12; height: 12
                MouseArea {
                  anchors.fill: parent; anchors.margins: -3; enabled: row.modelData.failed && root.retryEnabled; cursorShape: Qt.PointingHandCursor
                  onClicked: root.retryRequested()
                }
              }
            }
          }
          PanelToolTip {
            visible: mouse.containsMouse
            text: Model.wrappedPath(row.modelData.path) + (row.modelData.error ? "\n" + row.modelData.error : "") + (row.modelData.failed ? "\nRetry rechecks the complete sync safely." : "\nClick to show containing folder")
            fontFamily: root.fontFamily
          }
        }
      }
    }
  }
}
