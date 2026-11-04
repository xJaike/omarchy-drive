import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "io.github.xjaike.google-drive"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property string focusSection: "folders"
  property int folderIndex: 0
  property string cursorPath: ""
  property bool cursorActive: false

  readonly property var barIdentity: hostWidget || root
  readonly property var folderRows: Model.flattenRows(drive.folders, drive.treeChildren, drive.expandedPaths)
  readonly property bool driveActive: drive.authenticated && drive.tickedCount > 0
  readonly property string driveStatus: drive.statusText
  readonly property bool driveSyncing: drive.syncing
  readonly property string driveTooltip: drive.detailsTooltip
  readonly property string driveIconState: drive.iconState

  readonly property color foreground: Commons.Color.popups.text
  readonly property color urgent: Commons.Color.urgent
  readonly property color dim: Commons.Color.muted
  readonly property string fontFamily: Style.font.family
  readonly property color iconColor: drive.lastResult === "error"
    ? urgent
    : (drive.authenticated ? foreground : dim)
  readonly property color barIconColor: drive.authenticated
    ? (drive.lastResult === "error" ? urgent : barForeground)
    : Qt.darker(barForeground, 1.55)
  readonly property string lastSyncText: drive.syncing
    ? "Syncing now"
    : Model.relativeTime(drive.lastFinishedTs)

  function refresh() {
    drive.refresh()
    drive.refreshFolders()
  }

  function open() {
    refresh()
    drive.refetchTree()
    root.controller.show()
    Qt.callLater(function () {
      root.cursorActive = false
      if (panelFlick) panelFlick.contentY = 0
      keyCatcher.forceActiveFocus()
    })
  }

  function close() { root.controller.hide() }
  function toggle() { root.opened ? root.close() : root.open() }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function ensureCursor() {
    if (!drive.authenticated || root.folderRows.length === 0) {
      focusSection = "header"
      folderIndex = 0
      return
    }
    // Rows close under the cursor, so it follows its path to the nearest row still listed.
    var index = Model.indexOfPath(root.folderRows, cursorPath)
    folderIndex = Math.max(0, Math.min(root.folderRows.length - 1, index >= 0 ? index : folderIndex))
    cursorPath = root.folderRows[folderIndex].path
  }

  function moveCursor(dx, dy) {
    cursorActive = true
    pointerGate.reset()
    ensureCursor()
    if (focusSection === "folders" && dx !== 0) {
      stepTree(dx)
      return
    }
    if (dy === 0) return
    if (focusSection === "header") {
      if (dy > 0 && root.folderRows.length > 0) setCursorIndex(0)
      return
    }
    if (focusSection === "folders") {
      if (dy < 0 && folderIndex === 0) {
        focusSection = "header"
        if (panelFlick) panelFlick.contentY = 0
        return
      }
      setCursorIndex(Math.max(0, Math.min(root.folderRows.length - 1, folderIndex + dy)))
    }
  }

  function stepTree(dx) {
    var row = cursorRow()
    if (!row) return
    var isExpanded = drive.expandedPaths[row.path] === true
    if (dx > 0 ? !isExpanded && Model.canExpand(row) : isExpanded) drive.toggleExpanded(row.path)
  }

  function activateCursor() {
    ensureCursor()
    if (focusSection === "header") drive.setAutoSync(!drive.autoSyncActive)
    else if (focusSection === "folders") toggleRow(cursorRow())
  }

  function toggleRow(row) {
    if (!row) return
    if (Number(row.depth || 0) > 0) drive.toggleExcluded(row)
    else drive.toggleFolder(row)
  }

  function openCursorFolder() {
    if (!cursorActive || focusSection !== "folders") return
    var row = cursorRow()
    if (row) drive.openFolder(String(row.path).split("/")[0])
  }

  function cursorRow() {
    if (root.folderRows.length === 0) return null
    return root.folderRows[Math.max(0, Math.min(folderIndex, root.folderRows.length - 1))]
  }

  function setCursorIndex(index) {
    focusSection = "folders"
    folderIndex = index
    cursorPath = root.folderRows[index].path
    scrollCursorIntoView()
  }

  function setFolderCursor(index) {
    cursorActive = true
    setCursorIndex(index)
  }

  function scrollItemIntoView(item) {
    if (!panelFlick || !item) return
    Qt.callLater(function () {
      if (!item) return
      var margin = Style.space(6)
      var point = item.mapToItem(panelFlick.contentItem, 0, 0)
      var top = point.y
      var bottom = top + item.height
      var viewTop = panelFlick.contentY
      var viewBottom = viewTop + panelFlick.height
      var maxY = Math.max(0, panelFlick.contentHeight - panelFlick.height)
      if (top < viewTop + margin) panelFlick.contentY = Math.max(0, top - margin)
      else if (bottom > viewBottom - margin)
        panelFlick.contentY = Math.min(maxY, bottom + margin - panelFlick.height)
    })
  }

  function scrollCursorIntoView() {
    if (focusSection === "folders" && folderColumn && folderIndex >= 0 && folderIndex < folderColumn.children.length)
      scrollItemIntoView(folderColumn.children[folderIndex])
  }

  onFolderIndexChanged: scrollCursorIntoView()
  onFolderRowsChanged: {
    pointerGate.reset()
    ensureCursor()
    if (cursorActive) Qt.callLater(root.scrollCursorIntoView)
  }

  ThemeBridge {}

  // Rows are rebuilt under a resting pointer, which must not move the keyboard cursor.
  PointerMoveGate {
    id: pointerGate
    referenceItem: panelFlick
  }

  Service {
    id: drive
    settings: root.settings
  }

  Connections {
    target: drive
    function onAuthenticatedChanged() { root.ensureCursor() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    padding: 12
    contentWidth: panel.fittedContentWidth(400)
    contentHeight: panel.fittedContentHeight(column.implicitHeight, 620)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function (dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        root.moveCursor(dx, dy)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }
      onTextKey: function (t) {
        var key = String(t || "").toLowerCase()
        if (key === "s") drive.syncNow(false)
        else if (key === "a") drive.setAutoSync(!drive.autoSyncActive)
        else if (key === "b") drive.toggleBrowse()
        else if (key === "o") root.openCursorFolder()
        else if (key === "r") {
          root.refresh()
          drive.refetchTree()
        }
        else if (key === "c") drive.openBackups()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          Item {
            id: header
            width: parent.width
            implicitHeight: hero.implicitHeight
            readonly property bool ringVisible: root.cursorActive && root.focusSection === "header"

            RowLayout {
              id: hero
              width: parent.width
              spacing: 10
              GoogleDriveIcon { iconSize: 32; color: root.iconColor }
              ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                Text { text: "Google Drive"; color: root.foreground; font.family: root.fontFamily; font.pixelSize: 14; font.weight: Font.DemiBold }
                Text {
                  text: drive.authenticated ? Model.folderSummary({tickedCount: drive.tickedCount, localBytes: drive.localBytes, localBytesApprox: drive.localBytesApprox}) : "Not connected"
                  color: root.dim; font.family: root.fontFamily; font.pixelSize: 10
                  Layout.fillWidth: true; elide: Text.ElideRight
                }
              }
              Column {
                visible: drive.installed && drive.authenticated
                spacing: 4
                Rectangle {
                  width: 28; height: 14; radius: 7
                  anchors.horizontalCenter: parent.horizontalCenter
                  color: drive.autoSyncActive ? Commons.Color.accent : Util.alpha(root.foreground, 0.18)
                  border.color: header.ringVisible ? root.foreground : "transparent"
                  Rectangle { x: drive.autoSyncActive ? 16 : 2; y: 2; width: 10; height: 10; radius: 5; color: Commons.Color.background }
                  MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; enabled: !drive.busy; onClicked: drive.setAutoSync(!drive.autoSyncActive) }
                }
                Text { text: "AUTO-SYNC"; font.family: root.fontFamily; font.pixelSize: 9; color: root.dim }
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: text !== ""
            width: parent.width
            text: Model.panelNotice({actionStatus: drive.actionStatus, controlError: drive.controlError, lastError: drive.lastError,
                                     foldersError: drive.foldersError, warning: drive.warning})
            color: (drive.controlError !== "" || drive.lastError !== "" || drive.foldersError !== "") && drive.actionStatus === ""
              ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          Button {
            visible: drive.authenticated && drive.blockedPath !== "" && !drive.syncing
            radius: 3
            fontSize: 12
            text: Model.blockedAction(drive.blockedPath)
            enabled: !drive.busy
            foreground: root.foreground
            fontFamily: root.fontFamily
            bordered: true
            tooltipText: "Stop syncing " + Model.cleanText(drive.blockedPath) + ". No file changes on either side."
            onClicked: drive.excludeBlocked()
          }

          SetupNotice {
            visible: (!drive.authenticated || !drive.installed) && drive.statusOk
            width: parent.width
          }

          Column {
            visible: drive.authenticated
            width: parent.width
            spacing: Style.spacing.labelGap

            InfoPair { label: "On disk"; value: (drive.localBytesApprox ? "≈ " : "") + Model.formatBytes(drive.localBytes) }
            InfoPair {
              label: "Google storage"
              value: Model.usageText(drive.usedBytes, drive.quotaBytes, drive.quotaKnown)
            }
            InfoPair { label: "Last sync"; value: root.lastSyncText }
          }

          RowLayout {
            visible: drive.authenticated
            width: parent.width
            spacing: Style.space(6)

            Button {
              radius: 3
              fontSize: 12
              iconSize: 14
              horizontalPadding: 8
              verticalPadding: 6
              text: drive.syncing ? "Syncing…" : "Sync now"
              iconText: "󰑐"
              iconSpinning: drive.syncing
              enabled: !drive.busy && !drive.syncing && drive.authenticated && drive.installed
              foreground: root.foreground
              fontFamily: root.fontFamily
              tooltipText: "Run a two-way sync now  (s)"
              bordered: true
              Layout.fillWidth: true
              Layout.preferredWidth: 1
              Layout.minimumWidth: 0
              onClicked: drive.syncNow(false)
            }

            Button {
              radius: 3
              fontSize: 12
              iconSize: 14
              horizontalPadding: 8
              verticalPadding: 6
              text: "Backups"
              iconText: "󰩹"
              foreground: root.foreground
              fontFamily: root.fontFamily
              tooltipText: "Open local backup copies  (c)"
              bordered: true
              Layout.fillWidth: true
              Layout.preferredWidth: 1
              Layout.minimumWidth: 0
              onClicked: drive.openBackups()
            }

            Button {
              radius: 3
              fontSize: 12
              iconSize: 14
              horizontalPadding: 8
              verticalPadding: 6
              text: "Drive web"
              iconText: "󰇧"
              active: false
              enabled: !drive.busy
              foreground: root.foreground
              fontFamily: root.fontFamily
              tooltipText: "Open the configured Drive folder in your browser  (b)"
              Layout.fillWidth: true
              Layout.preferredWidth: 1
              Layout.minimumWidth: 0
              bordered: true
              onClicked: drive.toggleBrowse()
              onRightClicked: drive.openBrowse()
            }
          }

          RecentFiles {
            visible: drive.authenticated
            width: parent.width
            foreground: root.foreground
            urgent: root.urgent
            fontFamily: root.fontFamily
            current: drive.currentTransfer
            entries: drive.recentFiles
            remaining: drive.remainingChanges
            retryEnabled: drive.lastResult === "error" && !drive.busy && !drive.syncing && drive.installed
            onRevealRequested: function(path) { drive.revealFile(path) }
            onRetryRequested: drive.syncNow()
            onInteraction: root.cursorActive = false
          }

          PanelSeparator {
            visible: drive.authenticated
            foreground: root.foreground
          }

          Column {
            visible: drive.authenticated
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: "FOLDERS · " + drive.cloudFolderName.toUpperCase()
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              textFormat: Text.PlainText
              visible: drive.foldersLoading && root.folderRows.length === 0
              width: parent.width
              text: "Reading your folders…"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              horizontalAlignment: Text.AlignHCenter
            }

            Text {
              textFormat: Text.PlainText
              visible: !drive.driveAvailable
              width: parent.width
              text: drive.offlineNote
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Text {
              textFormat: Text.PlainText
              visible: !drive.foldersLoading && drive.foldersError === "" && root.folderRows.length === 0
              width: parent.width
              text: "No folders found in your home folder or Drive."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Column {
              id: folderColumn
              width: parent.width
              spacing: Style.space(4)

              Repeater {
                model: root.folderRows
                FolderRow {
                  required property var modelData
                  required property int index
                  width: folderColumn.width
                  row: modelData
                  rowIndex: index
                }
              }
            }
          }
        }
      }
    }
  }

  component SetupNotice: Column {
    spacing: Style.space(4)

    Text {
      textFormat: Text.PlainText
      width: parent.width
      text: !drive.installed ? "Install sync helpers" : "Connect Google Drive"
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
    }

    Text {
      textFormat: Text.PlainText
      width: parent.width
      text: "Use your own Google Desktop OAuth client. Setup opens in a terminal and transfers no files."
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }
    Button {
      text: drive.installed ? "Connect account" : "Install and connect"
      foreground: root.foreground
      fontFamily: root.fontFamily
      bordered: true
      onClicked: drive.configure()
    }
  }

  component FolderRow: CursorSurface {
    id: folderRow
    property var row: null
    property int rowIndex: 0
    readonly property int depth: row ? Number(row.depth || 0) : 0
    readonly property string rowPath: row ? String(row.path) : ""
    readonly property string rowName: row ? Model.cleanText(row.name || "Untitled") : "Untitled"
    readonly property bool checked: drive.isChecked(row)
    readonly property bool toggleable: Model.rowEnabled(row)
    readonly property bool tickable: Model.rowTickable(row, drive.syncing, drive.busy)
    readonly property bool expandable: Model.canExpand(row)
    readonly property bool expanded: drive.expandedPaths[rowPath] === true
    readonly property string caption: Model.cleanText(drive.rowCaption(row))
    readonly property string sizeText: Model.rowSize(row)

    hasCursor: root.cursorActive && root.focusSection === "folders" && root.folderIndex === rowIndex
    foreground: root.foreground
    implicitHeight: folderContent.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: folderRow.expandable ? Qt.PointingHandCursor : Qt.ArrowCursor
      onPositionChanged: function (mouse) {
        if (pointerGate.moved(folderRow, mouse)) root.setFolderCursor(folderRow.rowIndex)
      }
      onClicked: {
        root.setFolderCursor(folderRow.rowIndex)
        if (folderRow.expandable) drive.toggleExpanded(folderRow.rowPath)
      }
    }

    RowLayout {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(8)

      Text {
        textFormat: Text.PlainText
        text: folderRow.checked ? "󰄲" : "󰄱"
        color: folderRow.checked ? root.foreground : root.dim
        opacity: folderRow.tickable ? 1 : 0.5
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.preferredWidth: Style.space(20)
        Layout.alignment: Qt.AlignVCenter

        // Stays enabled when the row cannot be toggled, so a click here never expands the row instead.
        MouseArea {
          anchors.fill: parent
          cursorShape: folderRow.tickable ? Qt.PointingHandCursor : Qt.ArrowCursor
          onClicked: {
            root.setFolderCursor(folderRow.rowIndex)
            root.toggleRow(folderRow.row)
          }
        }
      }

      Text {
        textFormat: Text.PlainText
        text: folderRow.expandable ? (folderRow.expanded ? "▾" : "▸") : ""
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.leftMargin: Style.space(12) * folderRow.depth
        Layout.preferredWidth: Style.space(12)
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        id: folderContent
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: folderRow.rowName
          color: folderRow.toggleable ? root.foreground : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          visible: text !== ""
          Layout.fillWidth: true
          text: folderRow.caption
          color: drive.rowWarns(folderRow.row) ? root.urgent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }
      }

      Text {
        textFormat: Text.PlainText
        visible: text !== ""
        text: folderRow.sizeText
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        Layout.alignment: Qt.AlignVCenter
      }
    }
  }

  component InfoPair: Row {
    property string label: ""
    property string value: ""

    width: parent.width
    spacing: Style.space(8)

    InfoLabel { text: label }
    Item {
      width: Math.max(0, parent.width - parent.children[0].implicitWidth - parent.children[2].implicitWidth - parent.spacing * 2)
      height: 1
    }
    InfoValue { text: value }
  }

  component InfoLabel: Text {
    textFormat: Text.PlainText
    color: root.foreground
    opacity: 0.6
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  component InfoValue: Text {
    textFormat: Text.PlainText
    color: root.foreground
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    elide: Text.ElideRight
  }
}
