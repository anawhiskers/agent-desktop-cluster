# Manual closed SQLite backup archiving

This optional utility compresses one closed SQLite backup and removes its source
only after a decompression check, byte/hash comparison, SQLite integrity check
and durable verification journal. The owner must authorize that exact file.
Keep the newest retained backup unchanged and outside the archive allowlist.

The library uses Python's standard library and POSIX file APIs. It has no CLI;
import it from `tools/closed-sqlite-backup`. Its tests and complete plan/API
contract are in [the library README](../tools/closed-sqlite-backup/README.md).
No MCP tool, startup hook, daemon, schedule or retention policy is added.

## AI-agent workflow

1. Select one older backup from the owner's approved private allowlist. Confirm
   its recorded hash/identity, protect the newest backup, and verify that existing
   approval covers archive verification followed by one source unlink. Ask only
   when that scope is unclear. A newly calculated hash alone does not establish
   which historical file the owner authorized.
2. Keep the source and its related directories quiescent throughout the operation.
   Coordinate all backup writers first. The source must be a closed, single-link
   0600 owner file, without SQLite sidecars; immediate parents must be 0700 owner
   directories. Every path ancestor must be free of group/world write permission.
3. Prepare one exact schema-v1 plan privately, using the reviewed API contract.
   Set input/archive/time/space-reserve budgets and a new operation directory.
   Snapshot parent metadata after any earlier authorized operation has finished.
   Keep plans, mappings and journals outside the public checkout and AI prompts.
4. Call `archive_backup(plan)` once. Internally, it creates a new private archive,
   restores a verification copy, compares all bytes by hash, checks SQLite,
   writes/fsyncs `verified.json`, rechecks identities and hashes, then attempts
   one source unlink. A separate `retired.json` records successful completion.
5. Inspect the returned status and exact saved files before any next operation.
   `retired_durable` records unlink/fsync/journal completion. A retained or
   uncertain result requires private manual reconciliation. Preserve partial
   files and old journals; never replay the same plan or choose a replacement
   source automatically. The library does not enforce newest-backup protection;
   that remains the owner's allowlist decision.

For a separate restore, load the private verified mapping and supply a new,
owner-selected destination in a quiescent 0700 directory. This API creates only
that new 0600 file; it does not overwrite or install over the original:

```python
from archive_backup import restore_verified

restored = restore_verified(
    verified_mapping,
    new_destination,
    max_archive_bytes=approved_archive_budget,
    max_output_bytes=approved_restore_budget,
    max_seconds=approved_time_budget,
    reserve_bytes=approved_space_reserve,
)
assert restored["status"] == "restored_verified"
```

The variables above come from the owner's private plan. Failed restore attempts
may leave a partial destination for inspection. Replacing a live database is a
separate owner-authorized operation after application checks and a rollback plan.

## Limits and verification

`flock` is advisory. Sidecar absence does not prove that SQLite is closed. POSIX
has no atomic compare-and-unlink against an uncooperative process under the same
owner account. Final checks narrow races; the caller must maintain quiescence.
Space checks do not reserve blocks. Fsync durability depends on the filesystem,
and elapsed checks cannot interrupt a blocked filesystem syscall.

Run the standalone fixture suite from `tools/closed-sqlite-backup`:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_archive_backup.py
```

The suite creates and removes its own fictional SQLite fixtures. It covers
byte-identical restore and query results, source/archive mutation, identity,
links, journals, sidecars, budgets, unlink and fsync failures. These local tests
and the independent static review do not prove a target host's filesystem,
backup closure, available space or production allowlist.
