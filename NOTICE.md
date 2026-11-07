# Attribution

This is a fork of [hominluo/omarchy-google-drive](https://github.com/hominluo/omarchy-google-drive).
The interface is based on upstream commit `918def057e0d28fdd0f02b8f065b600234a6d29a`.

The panel layout, keyboard handling, formatting helpers and Google Drive icon
are adapted from Homin Luo's MIT-licensed work, which itself includes work by
Wesley Cole. Both copyright notices remain in LICENSE. The original upstream
notice is preserved in [docs/UPSTREAM_NOTICE.md](docs/UPSTREAM_NOTICE.md).

The backend is adapted from xJaike's standalone Omarchy Drive engine. It uses
Python's standard library and Google's Drive API directly. This fork does not
execute or require the previous sync engine or its FUSE mount.

The current backend includes persistent local checksum caching, concurrent
folder listings and file transfers, and bounded panel inventory work. These are
changes in this fork; see [Performance](docs/PERFORMANCE.md) and
[CHANGELOG.md](CHANGELOG.md) for behavior, measurements and limits. The preserved
upstream notice describes the earlier upstream implementation.

The Google Drive logo geometry and trademark notice are unchanged from upstream.
Google Drive is a trademark of Google LLC. This project is not affiliated with,
endorsed by, or sponsored by Google.

## Repository migration

The standalone-backend adaptation was first prepared in
[xJaike/omarchy-google-drive](https://github.com/xJaike/omarchy-google-drive)
at commit `2ee11a80e8b94029e4b9fcd84ee88e11ce1cd2e6`, then copied into
[xJaike/omarchy-drive](https://github.com/xJaike/omarchy-drive).
The original Omarchy Drive repository history is retained. The plugin ID remains
`io.github.xjaike.google-drive`, so the installed adapted plugin keeps its identity.
