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
  property var currentTransfer: null
  property var recentFiles: []
  property int remainingChanges: 0
  property string cloudFolderName: "Omarchy"
  property string cloudUrl: "https://drive.google.com/drive/my-drive"
  property int tickedCount: 0
  property string blockedPath: ""
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
  property string controlError: ""
  property var folders: []
  property bool driveAvailable: true
  property bool foldersLoading: false
  property string foldersError: ""
  property var pendingFolders: ({})
  property var treeChildren: ({})
  property var expandedPaths: ({})
  property var treeLoading: ({})
  property var treeErrors: ({})
  property int syncIntervalMin: 1
  property int _desiredTimer: -1
  property bool _foldersLoaded: false
  property bool _foldersAgain: false
  property var _treeQueue: []
  property string _treePath: ""
  property bool _treeDiscard: false

  readonly property string offlineNote: "Drive could not be read. Showing local folders only."
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
    currentTransfer = syncing ? (value.current || null) : null
    remainingChanges = Number(value.pending || 0)
    var recent = Array.isArray(value.recent) ? value.recent : []
    if (JSON.stringify(recent) !== JSON.stringify(recentFiles)) recentFiles = recent
    cloudFolderName = value.cloudFolderName || "Omarchy"
    cloudUrl = value.cloudUrl || "https://drive.google.com/drive/my-drive"
    tickedCount = Number(value.tickedCount || 0)
    blockedPath = value.blockedPath || ""
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
    driveAvailable = value.driveAvailable !== false
    foldersError = ""
  }
  function isSelected(folder) {
    var pending = pendingFolders[folder.name]
    return typeof pending === "boolean" ? pending : folder.selected === true
  }
  function isChecked(row) {
    if (!row) return false
    return Number(row.depth || 0) > 0 ? Model.childChecked(row) : isSelected(row)
  }
  function toggleFolder(folder) {
    if (!folder || busy || syncing || !Model.rowEnabled(folder)) return
    var next = !isSelected(folder)
    var copy = Object.assign({}, pendingFolders)
    copy[folder.name] = next
    pendingFolders = copy
    runControl(baseArgs("select").concat(["--name=" + folder.name, next ? "--enable" : "--disable"]))
  }
  function toggleExcluded(row) {
    if (!row || busy || syncing || !Model.rowEnabled(row)) return
    runControl(baseArgs(Model.includesOnToggle(row) ? "include" : "exclude").concat(["--path=" + row.path]))
  }
  function excludeBlocked() {
    if (blockedPath === "" || busy || syncing) return
    if (blockedPath.indexOf("/") === -1) runControl(baseArgs("select").concat(["--name=" + blockedPath, "--disable"]))
    else runControl(baseArgs("exclude").concat(["--path=" + blockedPath]))
  }
  function withEntry(map, key, value) {
    var copy = Object.assign({}, map)
    copy[key] = value
    return copy
  }
  function withoutEntry(map, key) {
    var copy = Object.assign({}, map)
    delete copy[key]
    return copy
  }
  function toggleExpanded(path) {
    if (expandedPaths[path] === true) { expandedPaths = withoutEntry(expandedPaths, path); return }
    expandedPaths = withEntry(expandedPaths, path, true)
    if (!Array.isArray(treeChildren[path])) requestTree(path)
  }
  function requestTree(path) {
    treeErrors = withoutEntry(treeErrors, path)
    if (treeLoading[path] === true) return
    treeLoading = withEntry(treeLoading, path, true)
    _treeQueue = _treeQueue.concat([path])
    pumpTree()
  }
  function pumpTree() {
    if (treeProcess.running || _treeQueue.length === 0) return
    _treePath = _treeQueue[0]
    _treeQueue = _treeQueue.slice(1)
    treeProcess.command = baseArgs("tree").concat(["--path=" + _treePath])
    treeProcess.running = true
    treeTimeout.restart()
  }
  function finishTree(code, out, err) {
    var value = code === 0 ? Model.parseTree(out) : {ok: false, lastError: String(err || "").trim()}
    if (value.ok === false) { failTree(_treePath, value.lastError || "This folder could not be read."); return }
    driveAvailable = value.driveAvailable !== false
    treeChildren = withEntry(treeChildren, _treePath, value.rows)
    treeLoading = withoutEntry(treeLoading, _treePath)
  }
  function failTree(path, message) {
    treeErrors = withEntry(treeErrors, path, message)
    treeLoading = withoutEntry(treeLoading, path)
    // Closed again, so that expanding it retries.
    expandedPaths = withoutEntry(expandedPaths, path)
  }
  function treeTimedOut() {
    failTree(_treePath, offlineNote)
    _treeDiscard = true
    treeProcess.running = false
  }
  function resetTree() {
    _treeQueue = []
    treeChildren = ({})
    expandedPaths = ({})
    treeLoading = ({})
    treeErrors = ({})
    if (treeProcess.running) {
      _treeDiscard = true
      treeTimeout.stop()
      treeProcess.running = false
    }
  }
  function rowNote(path) {
    if (treeLoading[path] === true) return "Loading…"
    if (typeof treeErrors[path] === "string") return treeErrors[path]
    var children = treeChildren[path]
    return expandedPaths[path] === true && Array.isArray(children) && children.length === 0 ? "Empty" : ""
  }
  function rowCaption(row) {
    if (!row) return ""
    return [Model.rowLabel(row, isChecked(row)), rowNote(row.path)].filter(Boolean).join(" · ")
  }
  function rowWarns(row) {
    return !!row && (typeof treeErrors[row.path] === "string" || Model.rowWarns(row))
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
  function revealFile(path) {
    if (revealProcess.running) return
    revealProcess.command = baseArgs("reveal").concat(["--path=" + String(path)])
    revealProcess.running = true
  }
  function openFolder(name) {
    var row = folders.find(function (folder) { return folder.name === name })
    if (homePath && row && row.where !== "drive" && !row.error) Quickshell.execDetached(["xdg-open", homePath + "/" + name])
  }
  function openBrowse() { Quickshell.execDetached(["xdg-open", cloudUrl]) }
  function toggleBrowse() { openBrowse() }
  function openBackups() { Quickshell.execDetached(baseArgs("open-backups")) }
  function runControl(command) {
    if (busy) return
    actionStatus = "Applying change…"
    controlError = ""
    controlProcess.command = command
    controlProcess.running = true
  }

  Timer { interval: root.syncing ? 1000 : root.refreshIntervalSec * 1000; repeat: true; running: true; triggeredOnStart: true; onTriggered: root.refresh() }
  Timer { id: folderTimeout; interval: 240000; onTriggered: { foldersProcess.running = false; root.foldersLoading = false; root.foldersError = "Folder listing timed out" } }
  Timer { id: treeTimeout; interval: 240000; onTriggered: root.treeTimedOut() }
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
      root.pendingFolders = ({})
      root.resetTree()
      root.refresh()
      if (root._foldersAgain) {
        root._foldersAgain = false
        Qt.callLater(root.refreshFolders)
      }
    }
  }
  Process {
    id: treeProcess
    stdout: StdioCollector { id: treeOut; waitForEnd: true }
    stderr: StdioCollector { id: treeErr; waitForEnd: true }
    onRunningChanged: if (!treeProcess.running) Qt.callLater(root.pumpTree)
    onExited: function(code) {
      treeTimeout.stop()
      var discard = root._treeDiscard
      root._treeDiscard = false
      if (!discard) root.finishTree(code, treeOut.text, treeErr.text)
    }
  }
  Process {
    id: revealProcess
    stderr: StdioCollector { id: revealErr; waitForEnd: true }
    stdout: StdioCollector { waitForEnd: true }
    onExited: function(code) {
      root.actionStatus = code === 0 ? "Containing folder opened" : String(revealErr.text || "Could not open containing folder")
      clearNote.restart()
    }
  }
  Process {
    id: controlProcess
    stdout: StdioCollector { id: controlOut; waitForEnd: true }
    stderr: StdioCollector { id: controlErr; waitForEnd: true }
    onExited: function(code) {
      root._desiredTimer = -1
      // On success the tick stays as shown until the folder list has caught up.
      if (code !== 0) root.pendingFolders = ({})
      root.controlError = code === 0 ? "" : String(controlErr.text || "The Drive command failed").trim()
      root.actionStatus = code === 0 ? "Updated" : ""
      clearNote.restart()
      root.resetTree()
      root.refresh()
      // A read already under way started before this change, so read again after it.
      if (foldersProcess.running) root._foldersAgain = true
      else root.refreshFolders()
    }
  }
  Timer { id: clearNote; interval: 2500; onTriggered: root.actionStatus = "" }
}
