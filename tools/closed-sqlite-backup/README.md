# Closed SQLite backup archive library

`archive_backup.py` archives one closed owner backup supplied by an exact plan. It does not search for backups, choose retention policy, replay failed work, or change historical receipts. The lead must provide the allowlist, protect the newest retained backup, confirm that each supplied source is closed, and inspect each returned result before selecting another operation.

Each operation uses a new 0700 directory, exclusive 0600 files, and directory descriptors opened without symlink traversal. Source files must be regular, single-link files owned by the current effective UID/GID with mode 0600. Immediate source and archive parents must currently match the supplied 0700 owner metadata; every ancestor must be free of group/world write permissions. The plan records exact source and immediate-parent metadata. Aliases, links, existing operation directories, SQLite sidecars, malformed plans, changed identities, and budget failures are refused.

## API and plan

Import `archive_backup` and call `archive_backup(plan)`. No command-line runner is supplied. Schema version 1 requires exactly these keys:

```python
{
    "version": 1,
    "source": "/absolute/canonical/closed.sqlite",
    "source_identity": identity(os.lstat(source)),
    "source_parent_identity": identity(os.lstat(source_parent), True),
    "sha256": "the independently recorded original SHA256",
    "archive_dir": "/absolute/private/archive-parent/new-operation",
    "archive_parent_identity": identity(os.lstat(archive_parent), True),
    "uid": os.geteuid(), "gid": os.getegid(),
    "max_input_bytes": 1048576,
    "max_archive_bytes": 1048576,
    "max_seconds": 60,
    "reserve_bytes": 268435456,
}
```

`identity` captures device, inode, UID/GID, permissions, modification/change timestamps, and for files, byte count and link count. Supply the source hash from the existing receipt; recording a new hash alone does not prove it is the authorized historical file. Parent snapshots must be refreshed independently when another authorized operation changes their directory timestamps. Input size is bounded at 1 TiB, elapsed checks at 1–3600 seconds, and archive size at the supplied cap. The free-space check reserves one complete decompressed copy, the maximum compressed archive, the requested reserve, and 64 KiB for journals. It does not reserve free blocks against another writer.

Compression preserves every logical input byte. Gzip has no original filename and a zero timestamp. Verification decompresses into `verify.sqlite`, checks full byte count and SHA256, and runs read-only immutable SQLite `PRAGMA integrity_check`. After verification, the temporary copy is removed. Failed work can leave this copy and partial archive files for manual inspection; no failed file is automatically retired.

`intent.json` records that an operation started. `verified.json` stores the restoration mapping and is fsynced with its directory before retirement. The source hash/identity, sidecars, canonical directory identities, verified archive hash/identity, and verified journal identity are rechecked before one source unlink. Full source-parent metadata is checked again after both final hashes; archive-parent metadata after operation creation and operation-directory metadata after the verified journal must also remain unchanged. The source directory is then fsynced. `retired.json` is a new completion receipt, never an edit to an earlier receipt.

## Results and restoration

`retired_durable` means the unlink, source-directory fsync, and new completion journal succeeded. `failed_original_retained` means this operation did not retire the source. `archive_verified_original_retained` means decompression/integrity verification succeeded at that stage, but source retirement did not complete. `archive_verified` and `journal_durable` record completed stages; external changes detected later can invalidate their contents, so check the error and restoration mapping before trusting retained files. `retirement_uncertain` means an unlink attempt began, or a later durability/journal step failed. Its `original` field reports observed presence, absence, changed identity, or inability to reconcile. Never automatically retry any of these states.

For manual restoration, load `verified.json` and call `restore_verified(receipt["mapping"], "/absolute/private/restore-parent/new.sqlite", max_archive_bytes=1048576, max_output_bytes=1048576, max_seconds=60, reserve_bytes=268435456)`. It requires a complete exact mapping schema and explicit compressed-byte, output-byte, elapsed-time, and free-space-reserve budgets. The destination must differ from both recorded original and archive paths. It creates a new 0600 file only, checks compressed hash/identity, then verifies decompressed bytes/hash and SQLite integrity, and fsyncs the destination directory. It refuses an existing destination or destination SQLite sidecars, and rechecks sidecar absence before reporting success. It never installs over the original path automatically. Failed restoration can leave a partial file; reconcile it manually. The mapping retains original path/hash/byte count and archive path/hash/byte count and exact archive identity.

## Limits

The caller must keep each source and its directories quiescent for the entire operation. `flock` is advisory, SQLite sidecars are evidence of activity but their absence does not prove closure, and POSIX has no atomic compare-and-unlink against an uncooperative concurrent owner process. Directory descriptors and final rechecks narrow races; they do not turn the owner UID into a security boundary. Storage durability depends on filesystem fsync behavior. Byte loops and SQLite progress checks enforce elapsed budgets at checkpoints; a blocked filesystem syscall cannot be forcibly timed out by this library. Python's gzip reader may accept valid concatenated streams, but exact decompressed byte count/hash still must match.

The utility uses only Python's standard library and POSIX file APIs. Tests ran on macOS; the actual Linux filesystem, ownership, SQLite build, backup closure, space reserve, and production allowlist still require lead verification. There is no daemon, scheduler, root wrapper, account change, credential access, or network call. `_checkpoint` is a private fault-injection hook used only by the offline suite.

## Offline verification

Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_archive_backup.py` in this directory. Fixtures are generated beneath this private scope and removed in `tearDown`; no process is launched outside the test interpreter. The suite exercises actual restoration byte equality and a SQLite query, then fault cases around hashes, identities, links, existing operation files, parents, archives, journals, unlink, sidecars, budgets, and corrupt SQLite.
