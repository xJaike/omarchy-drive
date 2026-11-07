# Performance: checks, transfers, and the panel

Updated 2026-11-07 for the performance implementation in commit `e8f042b`.
See [setup](STANDALONE_SETUP.md) for installation and [the changelog](../CHANGELOG.md)
for the complete change list.

## What runs during a check

Sync and preview walk the selected local directories and list the selected Drive
directories before planning changes. Excluded subtrees are pruned. A complete
snapshot is still required; this version has no filesystem watcher or Drive
change-feed service.

The first local scan computes MD5 checksums and persists them in the
`local_hashes` table of the state database. Later scans reuse a checksum only when
all six signature fields match: device, inode, size, nanosecond modification
time, nanosecond change time, and file mode. Change time catches ordinary edits
even when an application restores modification time. Metadata is checked again
after hashing or cache lookup, and a detected concurrent change stops the scan.

New, edited or replaced files are hashed again. Files changed by a transfer may
need one more hash on the next scan because transfer completion does not populate
the scan cache. A reboot or filesystem metadata change can also invalidate
entries. The cache is an optimization, separate from the baseline that records
sync history. Never delete the database to make a scan faster.

The cache relies on normal filesystem metadata behavior. It is not a substitute
for independent backups or an integrity audit. Before overwriting or archiving
an existing local file, the engine still verifies its contents. Cloud version
preconditions, backup verification, resumable transfers and deletion limits
remain in force.

## Which commands use the cache

| Operation | Work performed |
| --- | --- |
| `sync` | Cached local scan, complete selected Drive listing, then reconciliation |
| `preview` | Same cached snapshot and planning; no file-content transfers or deletions |
| `doctor` | Fresh local content hashing and Drive access checks; bypasses the scan cache |
| `status` / panel status poll | Reads local status and settings; no Drive listing or content hashing |
| Panel folder list | Reads Drive folder metadata and quota when its 15-second cache is stale; estimates local sizes |
| Expand a panel folder | Lists one level locally and on Drive; hashes no content |

Preview may refresh sign-in and update local bookkeeping, including the checksum
cache. It does not promise zero local state writes. Use `doctor` for diagnostics,
not as a prerequisite to every scheduled sync; its fresh hashing can take longer.

## Work that now scales better

- Drive folder listings use up to four workers. Pagination within one folder is
  sequential; selection, validation and progress reporting stay on the scan thread.
- Independent uploads and downloads share a separate four-worker transfer pool.
  Scanning finishes before transfers begin, so these are not eight simultaneous
  file transfers. Folder creation, conflicts and removals remain ordered barriers.
- Directory names and exclusion prefixes use set lookups. Shared ancestor safety
  checks are reused within each ignore batch. Git still evaluates `.gitignore`
  rules in batches, and a large number of directories can incur Git startup costs.
- The cache writes only new, changed or removed rows in one transaction. An
  unchanged cache writes no rows. Matching baseline records are not rewritten,
  and already-recorded matching files produce no bookkeeping actions.
- Progress uses an ordered queue with constant-time removal and reads at most
  20 waiting items for status. The panel displays the next three; the full queue
  is not rebuilt for each update. Status persistence and file checkpoints still
  have disk costs.

The panel uses directory-entry metadata for size estimates, skips symlinks,
special files and nested mounts, and checks its deadline even for empty folders.
Each folder gets at most 0.15 seconds of traversal, sharing one second across the
folder list. An unfinished estimate is approximate. If the shared budget is
exhausted before a folder starts, its previous size is reused if available,
otherwise zero is shown as approximate. These figures describe local disk usage,
not the exact amount of selected content awaiting transfer.

The budget bounds traversal work rather than individual filesystem calls; slow
storage can still block a call. Opening the folder list can also wait on network
requests, which are outside that size-estimation budget.

## Measurements

Local synthetic measurements taken on 2026-11-07:

| Operation | Before | After | Scope |
| --- | --- | --- | --- |
| Scan 256 unchanged files totaling 256 MiB | 0.441 s | 0.008 s | About 57× for local hashing versus a populated cache |
| Process a 5,000-file progress queue | 1.775 s | 0.207 s | About 8.6×; excludes status disk I/O |
| Traverse 1,500 folders and 1,500 files | 0.140 s | 0.112 s | About 1.3×; content digest mocked to isolate traversal |
| Check 5,000 siblings with 1,000 exclusions | 1.059 s | 0.187 s | About 5.7×; excludes Git process startup |
| Save 10,000 unchanged cache entries | 20,000 row operations | 0 row operations | Old delete/reinsert cache versus changed-row persistence |

These are individual-operation measurements, not end-to-end sync results or a
comparison with OneDrive or Google Drive for Windows. Filesystem caches were not
flushed; the checksum-cache measurement followed the full scan. Results depend
on file sizes, directory shape, exclusions, disk speed and network latency.

To reproduce the local checksum comparison without accessing your synced files
or account, run this from the repository root. It temporarily uses 256 MiB of
disk space and removes those test files afterward:

```bash
python3 - <<'PY'
from pathlib import Path
import tempfile
import time
from omarchy_drive.engine import scan_local

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    block = b'x' * (1024 * 1024)
    for number in range(256):
        (root / str(number)).write_bytes(block)
    cache = {}
    started = time.perf_counter()
    scan_local(root, fresh=cache)
    full = time.perf_counter() - started
    started = time.perf_counter()
    scan_local(root, cache=cache)
    cached = time.perf_counter() - started
    print(f'Full: {full:.3f}s; cached: {cached:.3f}s; ratio: {full / cached:.1f}x')
PY
```

## When checking still takes time

1. Check the phase and path in the panel. Local checking can mean first-run
   hashing or changed files; Drive checking can mean many network folder listings.
2. Let the first sync or preview finish. Running `doctor` repeatedly always
   rehashes local content and does not demonstrate repeat-sync cache speed.
3. Exclude unnecessary generated files using the folder list or local `.gitignore`
   rules. Excluding a directory avoids walking its entire subtree. Exclusion
   leaves existing local and cloud content in place.
4. Keep the state directory on reliable local storage. SQLite reads, durable
   checkpoints, staged uploads and backups contribute to total time. Do not
   disable durability or reset sync history to improve benchmark numbers.
5. For network delays or repeated failures, inspect `omarchy-drive status` and
   `journalctl --user -u omarchy-drive-sync.service -n 50 --no-pager`. Review logs
   before sharing because filenames and account information can be private.

The schedule waits until a run finishes before scheduling the next interval.
Changing the interval controls how often checking starts; it does not accelerate
an individual scan. There is no instant push sync, configurable worker count,
or Windows-style placeholder filesystem in this version.

## Verification

The performance update passed 366 Python tests and the JavaScript model checks.
Regression cases cover persistent checksum reuse, same-size edits with restored
modification time, changes during hashing, removed cache entries, changed-row
writes, baseline identity changes, out-of-order queue completion, shared
ancestor checks and inventory budgets. Tests use temporary or fake data and do
not benchmark a real Google account.

```bash
python3 -m unittest discover -s tests -v
node tests/model_flatten.test.js
```
