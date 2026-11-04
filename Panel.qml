import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
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
  property bool cursorActive: false

  readonly property var barIdentity: hostWidget || root
  readonly property var folderRows: drive.folders
  readonly property bool driveActive: drive.authenticated && (drive.selectedCount > 0 || drive.rootFiles)
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
  readonly property string displayFolder: Model.shortHomePath(drive.folderPath, Quickshell.env("HOME"))
  readonly property string lastSyncText: drive.syncing
    ? "Syncing now"
    : Model.relativeTime(drive.lastFinishedTs)

  function refresh() {
    drive.refresh()
    drive.refreshFolders()
  }

  function open() {
    refresh()
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
    if (!drive.authenticated) { focusSection = "header"; return }
    if (root.folderRows.length === 0) {
      if (focusSection === "folders") focusSection = "root"
      folderIndex = 0
      return
    }
    if (folderIndex >= root.folderRows.length) folderIndex = root.folderRows.length - 1
    if (folderIndex < 0) folderIndex = 0
  }

  function moveCursor(dx, dy) {
    cursorActive = true
    ensureCursor()
    if (dy === 0) return
    if (focusSection === "header") {
      if (dy > 0) focusSection = "root"
      return
    }
    if (focusSection === "root") {
      if (dy < 0) {
        focusSection = "header"
        if (panelFlick) panelFlick.contentY = 0
      } else if (root.folderRows.length > 0) {
        focusSection = "folders"
        folderIndex = 0
        scrollCursorIntoView()
      }
      return
    }
    if (focusSection === "folders") {
      if (dy < 0 && folderIndex === 0) {
        focusSection = "root"
        return
      }
      folderIndex = Math.max(0, Math.min(root.folderRows.length - 1, folderIndex + dy))
      scrollCursorIntoView()
    }
  }

  function activateCursor() {
    ensureCursor()
    if (focusSection === "header") drive.setAutoSync(!drive.autoSyncActive)
    else if (focusSection === "folders") drive.toggleFolder(selectedFolder())
    else if (focusSection === "root") drive.setRootFiles(!drive.rootFiles)
  }

  function selectedFolder() {
    if (root.folderRows.length === 0) return null
    return root.folderRows[Math.max(0, Math.min(folderIndex, root.folderRows.length - 1))]
  }

  function setFolderCursor(index) {
    cursorActive = true
    focusSection = "folders"
    folderIndex = index
    scrollCursorIntoView()
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

  ThemeBridge {}

  Service {
    id: drive
    settings: root.settings
  }

  Connections {
    target: drive
    function onFoldersChanged() { root.ensureCursor() }
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
        else if (key === "o") drive.openFolder()
        else if (key === "r") root.refresh()
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
                  text: drive.authenticated ? Model.folderSummary({selectedCount: drive.selectedCount, rootFiles: drive.rootFiles, localBytes: drive.localBytes, localBytesApprox: drive.localBytesApprox}) : "Not connected"
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
            text: drive.actionStatus !== "" ? drive.actionStatus
              : (drive.lastError !== "" ? drive.lastError
                : (drive.foldersError !== "" ? drive.foldersError : drive.warning))
            color: (drive.lastError !== "" || drive.foldersError !== "") && drive.actionStatus === ""
              ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
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
            InfoPair { label: "Folder"; value: root.displayFolder }
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
              text: "Open"
              iconText: "󰉋"
              foreground: root.foreground
              fontFamily: root.fontFamily
              tooltipText: "Open the synced folder  (o)"
              bordered: true
              Layout.fillWidth: true
              Layout.preferredWidth: 1
              Layout.minimumWidth: 0
              onClicked: drive.openFolder()
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
              text: "KEEP ON DISK · " + drive.cloudFolderName.toUpperCase()
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              textFormat: Text.PlainText
              visible: drive.foldersLoading && root.folderRows.length === 0
              width: parent.width
              text: "Reading your Drive folders…"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              horizontalAlignment: Text.AlignHCenter
            }

            RootFilesRow { width: parent.width }

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
                  folder: modelData
                  rowIndex: index
                }
              }
            }
          }

          Column {
            visible: drive.staleBytes > 0 || drive.staleCount > 0
            width: parent.width
            spacing: Style.space(6)

            PanelSeparator { foreground: root.foreground }

            RowLayout {
              width: parent.width
              spacing: Style.space(8)

              Text {
                textFormat: Text.PlainText
                Layout.fillWidth: true
                text: Model.formatBytes(drive.staleBytes) + " remains on disk for deselected items. Files are retained; re-select to sync again."
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
              }

              Button {
                text: "Backups"
                iconText: "󰩹"
                enabled: !drive.busy
                foreground: root.foreground
                fontFamily: root.fontFamily
                tooltipText: "Open local backup copies  (c)"
                onClicked: drive.openBackups()
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
    property var folder: null
    property int rowIndex: 0
    readonly property bool checked: drive.isSelected(folderRow.folder)
    readonly property string folderName: folder ? String(folder.name || "Untitled") : "Untitled"

    hasCursor: root.cursorActive && root.focusSection === "folders" && root.folderIndex === rowIndex
    foreground: root.foreground
    implicitHeight: folderContent.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: drive.busy ? Qt.ArrowCursor : Qt.PointingHandCursor
      onEntered: root.setFolderCursor(folderRow.rowIndex)
      onClicked: drive.toggleFolder(folderRow.folder)
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
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.preferredWidth: Style.space(20)
        Layout.alignment: Qt.AlignVCenter
      }

      Text {
        textFormat: Text.PlainText
        text: Model.folderGlyph({ selected: folderRow.checked })
        color: folderRow.folder && folderRow.folder.stale ? root.urgent : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.preferredWidth: Style.space(20)
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        id: folderContent
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: folderRow.folderName
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: Model.folderMeta(folderRow.folder)
          color: folderRow.folder && folderRow.folder.stale ? root.urgent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }
    }
  }

  component RootFilesRow: CursorSurface {
    id: rootRow
    hasCursor: root.cursorActive && root.focusSection === "root"
    foreground: root.foreground
    implicitHeight: rootContent.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: drive.busy ? Qt.ArrowCursor : Qt.PointingHandCursor
      onEntered: { root.cursorActive = true; root.focusSection = "root" }
      onClicked: drive.setRootFiles(!drive.rootFiles)
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
        text: drive.rootFiles ? "󰄲" : "󰄱"
        color: drive.rootFiles ? root.foreground : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.preferredWidth: Style.space(20)
        Layout.alignment: Qt.AlignVCenter
      }

      Text {
        textFormat: Text.PlainText
        text: "󰈔"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.preferredWidth: Style.space(20)
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        id: rootContent
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: "Loose files in " + drive.cloudFolderName
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: Model.rootFilesMeta(drive.rootFileCount, drive.rootFileBytes,
                                    drive.rootLocalCount, drive.rootLocalBytes)
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
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
