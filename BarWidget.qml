import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.xjaike.google-drive"

  property bool showDetails: false
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true
    : false
  readonly property color iconColor: panelLoader.item
    ? panelLoader.item.barIconColor
    : (bar ? bar.barForeground : Color.foreground)
  readonly property bool driveActive: panelLoader.item ? panelLoader.item.driveActive === true : false
  readonly property bool driveSyncing: panelLoader.item ? panelLoader.item.driveSyncing === true : false
  readonly property string driveStatus: panelLoader.item ? panelLoader.item.driveStatus : "Checking…"

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function refresh() { if (panelLoader.item && panelLoader.item.refresh) panelLoader.item.refresh() }
  function open() { if (panelLoader.item && panelLoader.item.open) panelLoader.item.open() }
  function close() { if (panelLoader.item && panelLoader.item.close) panelLoader.item.close() }
  function toggle() { if (panelLoader.item && panelLoader.item.toggle) panelLoader.item.toggle() }

  function closeForPopoutSwitch() {
    if (panelLoader.item && panelLoader.item.closeForPopoutSwitch)
      panelLoader.item.closeForPopoutSwitch()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  IpcHandler {
    target: root.moduleName
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refresh(); return "ok" }
    function status(): string { return root.driveStatus }
  }

  Timer { id: hoverDelay; interval: 400; onTriggered: root.showDetails = button.tooltipHovered }
  DriveTooltip {
    target: button
    opened: root.showDetails && !root.opened
    text: panelLoader.item ? panelLoader.item.driveTooltip : "Google Drive · Checking…"
    position: root.bar ? root.bar.position : "top"
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: ""
    onTooltipHoveredChanged: {
      root.showDetails = false
      if (tooltipHovered) { hoverDelay.restart(); root.refresh() }
      else hoverDelay.stop()
    }
    iconComponent: Component {
      Item {
        GoogleDriveIcon {
          id: glyph
          anchors.centerIn: parent
          iconSize: Style.space(12)
          color: root.iconColor
          state: panelLoader.item ? panelLoader.item.driveIconState : "setup"
          opacity: root.driveActive ? 1.0 : 0.6

          // A slow pulse while the standalone backend works, so a long sync is
          // visible from the bar without opening the panel.
          SequentialAnimation on opacity {
            running: root.driveSyncing
            loops: Animation.Infinite
            alwaysRunToEnd: true
            NumberAnimation { to: 0.35; duration: 900; easing.type: Easing.InOutQuad }
            NumberAnimation { to: 1.0; duration: 900; easing.type: Easing.InOutQuad }
          }
        }
      }
    }
    onPressed: function (buttonCode) {
      root.showDetails = false
      hoverDelay.stop()
      if (buttonCode === Qt.RightButton) root.refresh()
      else root.toggle()
    }
  }
}
