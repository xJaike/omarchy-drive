// Pure helpers for the Google Drive folder widget. Kept free of QML types so
// the parsing and formatting can be reasoned about (and tested) on their own.

var FOLDER_GLYPH = "󰉋"
var SYNCED_GLYPH = "󰉖"

function defaultStatus() {
  return {
    ok: true,
    installed: false,
    authenticated: false,
    syncing: false,
    statusText: "Unavailable",
    folderPath: "",
    mountPath: "",
    remoteName: "",
    selectedCount: 0,
    rootFiles: true,
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
    return { ok: false, folders: [], staleBytes: 0, rootFiles: true, lastError: "" }
  }, "Failed to parse the Google Drive folder list")
  parsed.folders = Array.isArray(parsed.folders) ? parsed.folders : []
  parsed.staleBytes = Number(parsed.staleBytes || 0)
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
  var count = Number(status.selectedCount || 0)
  var parts = []
  parts.push(count === 0 ? "No folders" : (count === 1 ? "1 folder" : count + " folders"))
  if (status.rootFiles) parts.push("root files")
  parts.push((status.localBytesApprox ? "≈ " : "") + formatBytes(status.localBytes) + " on disk")
  return parts.join(" · ")
}

function folderMeta(folder) {
  if (!folder) return ""
  var bytes = (folder.approx ? "≈ " : "") + formatBytes(folder.localBytes)
  if (folder.missing) return "Not in Drive · " + bytes + " on disk"
  if (folder.stale) return "No longer syncing · " + bytes
  if (folder.selected) return folder.onDisk ? bytes + " on disk" : "Waiting for first sync"
  return "Not synced"
}

function folderGlyph(folder) {
  return folder && folder.selected ? SYNCED_GLYPH : FOLDER_GLYPH
}

// The loose files at the very top of Drive, which belong to no folder.
function rootFilesMeta(count, bytes, localCount, localBytes) {
  var n = Number(count || 0)
  var have = Number(localCount || 0)
  var onDisk = Number(localBytes || 0)
  var kept = have > 0 ? " · " + formatBytes(onDisk) + " on disk" : ""
  if (n <= 0) return have > 0 ? have + (have === 1 ? " file" : " files") + kept : "Files that sit in no folder"
  return n + (n === 1 ? " file" : " files") + " · " + formatBytes(bytes) + kept
}

function shortHomePath(path, home) {
  var value = String(path || "")
  var prefix = String(home || "")
  if (prefix !== "" && value === prefix) return "~"
  if (prefix !== "" && value.indexOf(prefix + "/") === 0) return "~" + value.substring(prefix.length)
  return value
}

if (typeof module !== "undefined") {
  module.exports = {
    defaultStatus: defaultStatus,
    parseStatus: parseStatus,
    parseFolders: parseFolders,
    formatBytes: formatBytes,
    relativeTime: relativeTime,
    usageText: usageText,
    folderSummary: folderSummary,
    folderMeta: folderMeta,
    folderGlyph: folderGlyph,
    rootFilesMeta: rootFilesMeta,
    shortHomePath: shortHomePath
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
