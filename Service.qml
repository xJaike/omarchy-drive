import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import "Model.js" as Model

Item {
  id: root
  property var settings: ({})
  property bool installed: false
  property bool authenticated: false
  property bool syncing: false
  property bool timerEnabled: false
  property bool syncBaseline: false
  property bool statusOk: true
  property string statusText: "Checking…"
  property string iconState: "setup"
  property string detailsTooltip: "Checking Google Drive…"
  property string activityText: ""
  property string folderPath: ""
  property string cloudFolderName: "Omarchy"
  property string cloudUrl: "https://drive.google.com/drive/my-drive"
  property int selectedCount: 0
  property bool rootFiles: true
  property double localBytes: 0
  property bool localBytesApprox: false
  property double usedBytes: 0
  property double quotaBytes: 0
  property bool quotaKnown: false
  property string lastResult: ""
  property double lastFinishedTs: 0
  property string warning: ""
  property string lastError: ""
  property string actionStatus: ""
  property var folders: []
  property double staleBytes: 0
  property int staleCount: 0
  property int rootFileCount: 0
  property double rootFileBytes: 0
  property double rootLocalBytes: 0
  property int rootLocalCount: 0
  property bool foldersLoading: false
  property string foldersError: ""
  property var pendingFolders: ({})
  property int syncIntervalMin: 1
  property int _desiredTimer: -1
  property bool _foldersLoaded: false

  readonly property string homePath: Quickshell.env("HOME") || ""
  readonly property int refreshIntervalSec: Math.max(1, Number(settings.refreshIntervalSec || 5))
  readonly property bool busy: controlProcess.running
  readonly property bool autoSyncActive: _desiredTimer === -1 ? timerEnabled : (_desiredTimer === 1)
  readonly property string helperPath: decodeURIComponent(String(Qt.resolvedUrl("gdrive-sync.py")).replace(/^file:\/\//, ""))
  readonly property string configurePath: decodeURIComponent(String(Qt.resolvedUrl("configure.py")).replace(/^file:\/\//, ""))

  function baseArgs(command) { return ["python3", helperPath, command] }
  function refresh() {
    if (!statusProcess.running) {
      statusProcess.command = baseArgs("status")
      statusProcess.running = true
    }
  }
  function refreshFolders() {
    if (!authenticated || foldersProcess.running) return
    foldersLoading = true
    foldersProcess.command = baseArgs("folders")
    foldersProcess.running = true
    folderTimeout.restart()
  }
  function applyStatus(raw) {
    var value = Model.parseStatus(raw)
    statusOk = value.ok !== false
    if (!statusOk) { lastError = value.lastError || "Status unavailable"; return }
    installed = value.installed === true
    authenticated = value.authenticated === true
    syncing = value.syncing === true
    timerEnabled = value.timerEnabled === true
    syncBaseline = value.baseline === true
    statusText = value.statusText || "Unavailable"
    iconState = value.iconState || "setup"
    detailsTooltip = value.detailsTooltip || statusText
    activityText = value.activityText || ""
    folderPath = value.folderPath || ""
    cloudFolderName = value.cloudFolderName || "Omarchy"
    cloudUrl = value.cloudUrl || "https://drive.google.com/drive/my-drive"
    selectedCount = Number(value.selectedCount || 0)
    rootFiles = value.rootFiles !== false
    localBytes = Number(value.localBytes || 0)
    localBytesApprox = value.localBytesApprox === true
    usedBytes = Number(value.usedBytes || 0)
    quotaBytes = Number(value.quotaBytes || 0)
    quotaKnown = value.quotaKnown === true
    lastResult = value.lastResult || ""
    lastFinishedTs = Number(value.lastFinishedTs || 0)
    syncIntervalMin = Number(value.syncIntervalMin || 1)
    warning = value.warning || ""
    lastError = value.lastError || ""
    if (_desiredTimer !== -1 && timerEnabled === (_desiredTimer === 1)) _desiredTimer = -1
    if (authenticated && !_foldersLoaded && !foldersLoading) refreshFolders()
  }
  function applyFolders(raw) {
    var value = Model.parseFolders(raw)
    _foldersLoaded = true
    if (value.ok === false) { foldersError = value.lastError || "Could not list Drive folders"; return }
    folders = value.folders
    staleBytes = value.staleBytes
    staleCount = Number(value.staleCount || 0)
    rootFileCount = Number(value.rootFileCount || 0)
    rootFileBytes = Number(value.rootFileBytes || 0)
    rootLocalBytes = Number(value.rootLocalBytes || 0)
    rootLocalCount = Number(value.rootLocalCount || 0)
    rootFiles = value.rootFiles !== false
    foldersError = ""
    pendingFolders = ({})
  }
  function isSelected(folder) {
    var pending = pendingFolders[folder.name]
    return pending === undefined ? folder.selected === true : pending === true
  }
  function toggleFolder(folder) {
    if (!folder || busy || syncing) return
    var next = !isSelected(folder)
    var copy = Object.assign({}, pendingFolders)
    copy[folder.name] = next
    pendingFolders = copy
    runControl(baseArgs("select").concat([(next ? "--add=" : "--remove=") + folder.name]))
  }
  function setRootFiles(enabled) {
    if (!busy && !syncing) runControl(baseArgs("select").concat([enabled ? "--root-files" : "--no-root-files"]))
  }
  function syncNow() {
    if (!busy && !syncing && installed && authenticated) runControl(baseArgs("sync"))
  }
  function setAutoSync(enabled) {
    if (busy || !installed || !authenticated) return
    _desiredTimer = enabled ? 1 : 0
    var interval = Math.max(1, Number(settings.syncIntervalMin || syncIntervalMin))
    runControl(baseArgs("timer").concat([enabled ? "--enable" : "--disable", "--interval", String(interval)]))
  }
  function configure() { Quickshell.execDetached(["omarchy-launch-terminal", "python3", configurePath]) }
  function openFolder() { if (folderPath) Quickshell.execDetached(["xdg-open", folderPath]) }
  function openBrowse() { Quickshell.execDetached(["xdg-open", cloudUrl]) }
  function toggleBrowse() { openBrowse() }
  function openBackups() { Quickshell.execDetached(baseArgs("open-backups")) }
  function runControl(command) {
    if (busy) return
    actionStatus = "Applying change…"
    controlProcess.command = command
    controlProcess.running = true
  }

  Timer { interval: root.syncing ? 1000 : root.refreshIntervalSec * 1000; repeat: true; running: true; triggeredOnStart: true; onTriggered: root.refresh() }
  Timer { id: folderTimeout; interval: 240000; onTriggered: { foldersProcess.running = false; root.foldersLoading = false; root.foldersError = "Folder listing timed out" } }
  Process {
    id: statusProcess
    stdout: StdioCollector { id: statusOut; waitForEnd: true }
    stderr: StdioCollector { id: statusErr; waitForEnd: true }
    onExited: function(code) {
      if (code === 0) root.applyStatus(statusOut.text)
      else { root.statusOk = false; root.lastError = String(statusErr.text || "Python 3.11+ is required for the Drive backend") }
    }
  }
  Process {
    id: foldersProcess
    stdout: StdioCollector { id: foldersOut; waitForEnd: true }
    stderr: StdioCollector { id: foldersErr; waitForEnd: true }
    onExited: function(code) {
      folderTimeout.stop()
      root.foldersLoading = false
      if (code === 0) root.applyFolders(foldersOut.text)
      else { root._foldersLoaded = true; root.foldersError = String(foldersErr.text || "Could not list folders") }
      root.refresh()
    }
  }
  Process {
    id: controlProcess
    stdout: StdioCollector { id: controlOut; waitForEnd: true }
    stderr: StdioCollector { id: controlErr; waitForEnd: true }
    onExited: function(code) {
      root._desiredTimer = -1
      root.pendingFolders = ({})
      root.lastError = code === 0 ? "" : String(controlErr.text || "The Drive command failed")
      root.actionStatus = code === 0 ? "Updated" : ""
      clearNote.restart()
      root.refresh()
      root.refreshFolders()
    }
  }
  Timer { id: clearNote; interval: 2500; onTriggered: root.actionStatus = "" }
}
