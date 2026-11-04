// Pure helpers for the Google Drive folder widget. Kept free of QML types so
// the parsing and formatting can be reasoned about (and tested) on their own.

function defaultStatus() {
  return {
    ok: true,
    installed: false,
    authenticated: false,
    syncing: false,
    statusText: "Unavailable",
    mountPath: "",
    remoteName: "",
    tickedCount: 0,
    blockedPath: "",
    localBytes: 0,
    usedBytes: 0,
    quotaBytes: 0,
    usagePercent: 0,
    quotaKnown: false,
    browseMounted: false,
    timerEnabled: false,
    lastResult: "",
    lastFinishedTs: 0,
    lastDurationSec: 0,
    baseline: false,
    warning: "",
    lastError: ""
  }
}

function parseJson(raw, fallbackFactory, failureMessage) {
  var text = String(raw || "").trim()
  if (text === "") return fallbackFactory()
  try {
    var parsed = JSON.parse(text)
    if (!parsed || typeof parsed !== "object") return fallbackFactory()
    return parsed
  } catch (e) {
    var failed = fallbackFactory()
    failed.ok = false
    failed.lastError = failureMessage
    return failed
  }
}

function parseStatus(raw) {
  return parseJson(raw, defaultStatus, "Failed to parse Google Drive status")
}

function parseFolders(raw) {
  var parsed = parseJson(raw, function () {
    return { ok: false, folders: [], lastError: "" }
  }, "Failed to parse the Google Drive folder list")
  parsed.folders = Array.isArray(parsed.folders) ? parsed.folders : []
  return parsed
}

function parseTree(raw) {
  var parsed = parseJson(raw, function () {
    return { ok: false, rows: [], lastError: "" }
  }, "Failed to parse the folder contents")
  parsed.rows = Array.isArray(parsed.rows) ? parsed.rows : []
  return parsed
}

function formatBytes(bytes) {
  var value = Number(bytes || 0)
  if (!isFinite(value) || value <= 0) return "0 B"
  var units = ["B", "KB", "MB", "GB", "TB", "PB"]
  var index = 0
  while (value >= 1000 && index < units.length - 1) {
    value = value / 1000
    index++
  }
  var decimals = value >= 100 || index === 0 ? 0 : (value >= 10 ? 1 : 2)
  return value.toFixed(decimals).replace(/\.0+$/, "").replace(/(\.\d)0$/, "$1") + " " + units[index]
}

function relativeTime(timestampSec, nowMs) {
  var ts = Number(timestampSec || 0)
  if (!isFinite(ts) || ts <= 0) return "Never"
  var now = nowMs === undefined ? Date.now() : Number(nowMs)
  var diff = Math.max(0, Math.floor((now - ts * 1000) / 1000))
  if (diff < 45) return "Just now"
  var minutes = Math.floor(diff / 60)
  if (minutes < 60) return Math.max(1, minutes) + "m ago"
  var hours = Math.floor(minutes / 60)
  if (hours < 24) return hours + "h ago"
  var days = Math.floor(hours / 24)
  if (days < 30) return days + "d ago"
  var months = Math.floor(days / 30)
  if (months < 12) return months + "mo ago"
  return Math.floor(days / 365) + "y ago"
}

function usageText(usedBytes, quotaBytes, quotaKnown) {
  if (quotaKnown && Number(quotaBytes || 0) > 0)
    return formatBytes(usedBytes) + " of " + formatBytes(quotaBytes)
  return formatBytes(usedBytes)
}

// One line describing what the folder currently holds.
function folderSummary(status) {
  if (!status) return ""
  var count = Number(status.tickedCount || 0)
  var parts = []
  parts.push(count === 0 ? "No folders" : (count === 1 ? "1 folder" : count + " folders"))
  parts.push((status.localBytesApprox ? "≈ " : "") + formatBytes(status.localBytes) + " on disk")
  return parts.join(" · ")
}

// The list as shown: each expanded row is followed by its loaded children.
function flattenRows(folders, childrenByPath, expanded) {
  var rows = []
  function visit(row, path, depth) {
    rows.push(Object.assign({}, row, {path: path, depth: depth}))
    // Exact checks, because a folder can be named after an Object.prototype member.
    if (expanded[path] !== true || !Array.isArray(childrenByPath[path])) return
    childrenByPath[path].forEach(function (child) { visit(child, child.path, depth + 1) })
  }
  folders.forEach(function (folder) { visit(folder, folder.name, 0) })
  return rows
}

function isChild(row) {
  return Number(row.depth || 0) > 0
}

// tree does not refuse a mount point, so kind alone is not enough to offer expanding.
function canExpand(row) {
  if (!row) return false
  if (!isChild(row)) return !row.error
  return row.kind === "dir" && !row.unsupported
}

function includesOnToggle(row) {
  return row.excluded === true && row.inheritedExclusion !== true
}

function rowEnabled(row) {
  if (!row) return false
  if (!isChild(row)) return row.selected === true || !row.error
  return row.excludable === true || includesOnToggle(row)
}

// A ticked child is one that syncs, so excluded rows and Google Docs read as unticked.
function childChecked(row) {
  return row.excluded !== true && row.excludable === true
}

function rowWarns(row) {
  if (!isChild(row)) return row.selected === true && !!row.error
  return !!row.unsupported && row.excludable === true && row.excluded !== true
}

function rowLabel(row, checked) {
  if (!row) return ""
  if (isChild(row)) {
    if (row.excluded === true || row.inheritedExclusion === true) return "excluded"
    return row.unsupported || ""
  }
  if (row.error) return row.error
  if (row.where === "both") return "Home + Drive"
  if (row.where === "drive") return checked ? "Drive only · will create ~/" + row.name : "Drive only"
  return "Home only"
}

function rowSize(row) {
  if (!row) return ""
  if (isChild(row)) return row.kind === "file" ? formatBytes(row.size) : ""
  if (row.where === "drive" || row.error) return ""
  return (row.approx ? "≈ " : "") + formatBytes(row.localBytes)
}

// set_excluded refuses a top-level name, so unticking is the only remedy there.
function blockedAction(path) {
  return String(path || "").indexOf("/") === -1 ? "Untick this" : "Exclude this"
}

// The row at path, or its nearest visible ancestor; -1 when neither is listed.
function indexOfPath(rows, path) {
  var parts = String(path || "").split("/")
  while (parts.length > 0) {
    var wanted = parts.join("/")
    for (var i = 0; i < rows.length; i++)
      if (rows[i].path === wanted) return i
    parts.pop()
  }
  return -1
}

if (typeof module !== "undefined") {
  module.exports = {
    defaultStatus: defaultStatus,
    parseStatus: parseStatus,
    parseFolders: parseFolders,
    parseTree: parseTree,
    formatBytes: formatBytes,
    relativeTime: relativeTime,
    usageText: usageText,
    folderSummary: folderSummary,
    flattenRows: flattenRows,
    canExpand: canExpand,
    includesOnToggle: includesOnToggle,
    rowEnabled: rowEnabled,
    childChecked: childChecked,
    rowWarns: rowWarns,
    rowLabel: rowLabel,
    rowSize: rowSize,
    blockedAction: blockedAction,
    indexOfPath: indexOfPath
  }
}

// Keep activity presentation separate from raw paths and transfer bookkeeping.
function fileName(path) {
  var parts = String(path || "").split("/")
  return cleanText(parts.pop() || "Untitled file")
}

function parentPath(path) {
  var parts = String(path || "").split("/")
  parts.pop()
  return cleanText(parts.join("/") || "Synced folder")
}

function cleanText(value) {
  return String(value || "").replace(/[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/g, " ")
}

function actionLabel(action, failed) {
  if (failed) return ({upload: "Upload failed", download: "Download failed"})[action] || "Sync failed"
  return ({upload: "Uploaded", download: "Downloaded", conflict: "Both versions kept",
    "mkdir-local": "Folder created", "mkdir-remote": "Folder created",
    "archive-local": "Archived locally", "trash-remote": "Moved to Trash"})[action] || "Synced"
}

function activityGlyph(action, failed) {
  if (failed) return "!"
  return ({upload: "↑", download: "↓", conflict: "⇄", "mkdir-local": "+",
    "mkdir-remote": "+", "archive-local": "↙", "trash-remote": "−"})[action] || "✓"
}

function recentFileRows(entries) {
  var rows = [], seen = ({})
  var ordered = (Array.isArray(entries) ? entries : []).filter(function(entry) {
    return entry && typeof entry === "object" && entry.path
  }).sort(function(a, b) {
    return Number(b.time || 0) - Number(a.time || 0)
  })
  for (var i = 0; i < ordered.length; i++) {
    var entry = ordered[i]
    if (!entry || !entry.path) continue
    var key = "$" + entry.path
    if (seen[key]) continue
    seen[key] = true
    var failed = entry.state === "failed"
    rows.push({path: entry.path, name: fileName(entry.path), parent: parentPath(entry.path),
      action: entry.action || "sync", label: actionLabel(entry.action, failed),
      glyph: activityGlyph(entry.action, failed), failed: failed,
      time: Number(entry.time || 0), size: Number(entry.size || 0), error: cleanText(entry.error || "")})
  }
  return rows
}

if (typeof module !== "undefined") {
  Object.assign(module.exports, {fileName: fileName, parentPath: parentPath,
    cleanText: cleanText, actionLabel: actionLabel, activityGlyph: activityGlyph,
    recentFileRows: recentFileRows})
}

function transferEta(seconds) {
  var value = Math.max(0, Math.ceil(Number(seconds) || 0))
  if (value < 60) return value + "s"
  if (value < 3600) return Math.floor(value / 60) + "m " + (value % 60) + "s"
  return Math.floor(value / 3600) + "h " + Math.floor((value % 3600) / 60) + "m"
}

function wrappedPath(value) {
  var text = cleanText(value), lines = []
  while (text.length > 56) { lines.push(text.slice(0,56)); text = text.slice(56) }
  lines.push(text)
  return lines.join("\n")
}
if (typeof module !== "undefined") Object.assign(module.exports, {transferEta:transferEta, wrappedPath:wrappedPath})
