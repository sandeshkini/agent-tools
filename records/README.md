# records: aibo-mac's nightly database backups and the crew/card record

One nightly job on aibo-mac, the pa-app **Records Backup** (`com.sandesh.pa.records-backup`, 02:10,
log `/tmp/pa.records-backup.log`). Stdlib only. It doesn't stop, restart or write to any app.

| Step | What | Where it goes |
|---|---|---|
| Snapshots | `cos.db` (inside `cos-hub-1`) and cptr's `~/.cptr/app.db`, each copied with `VACUUM INTO` on a read-only connection (a consistent copy while the apps run), checked with `pragma integrity_check`, gzipped, with a `manifest.json` (sizes, sha256, row counts per table) | aibo-linux `~/Documents/backups/aibo-mac/<date>/` (last 7 days; pushed to `<date>.part`, checksums compared, then swapped in). aibo-linux's 02:30 `backup.sh` syncs that folder to B2 `b2crypt:aibo-mac`. A local copy of the last 3 days stays in `~/.local/share/records-backup/` |
| Crew record | every crew message from `messaging.db` on aibo-linux | `ai-memory/records/crew/YYYY-MM.jsonl` |
| Card record | every card and his answer, from tonight's cos.db snapshot | `ai-memory/records/cards/YYYY-MM.jsonl` |

- **Why:** aibo-mac has no B2. Its backup is git, and neither database is in a repo, so before this neither had a
  copy off the Mac.
- **The record files:**
  - are merged by id, so rows the live databases delete later stay in the record;
  - are scrubbed twice: on aibo-linux with its `keys.env`, then here with this Mac's (`scrub.py`, a copy of the
    librarian's `knowledge/tools/scrub.py`). It removes keys.env values, token patterns, IPs, emails and account
    and phone numbers;
  - go through a final leak check before they're kept;
  - are committed and pushed by `sync-repos` (ai-memory's data policy).

## Run, check, restore

```bash
python3 -I nightly.py                       # everything (what the app runs)
python3 -I nightly.py --only snapshots      # or: --only records
tail /tmp/pa.records-backup.log

# Restore check of one night's copy (here: straight from B2)
ssh aibo-linux 'rclone --config ~/.config/rclone/rclone.conf copy b2crypt:aibo-mac/<date> /tmp/r'
scp 'aibo-linux:/tmp/r/*' /tmp/r/ && python3 -I nightly.py --restore-check /tmp/r
```

**Restoring for real** needs the owner's OK. It replaces live state.
- **cos.db:**
  1. Stop the hub, using the idle gate (`scripts/when-chief-idle.sh`).
  2. `gunzip` the copy.
  3. `docker cp` it into the `cos_hub-data` volume as `/data/cos.db`, after removing `cos.db-wal` and `cos.db-shm`.
  4. Start the hub.
- **cptr:**
  1. Quit cptr.
  2. Replace `~/.cptr/app.db`, and remove its `-wal` and `-shm`.
  3. Start cptr.
- Colima doesn't share the Mac's `/tmp` or `~/.local` with containers, so use `docker cp` or a volume, not a bind
  mount, when testing a copy in a container.

Verified 2026-10-17:
- **Both copies:** fetched back from B2, they matched their manifests (sha256, `integrity_check`, row counts).
- **cos.db:** a throwaway hub with no network and no config, booted on the restored file in a scratch volume,
  served all 231 cards (50 with his answers), 139 tasks and 214 agents.
- **cptr's app.db:** the copy had the same schema and migration (`0004`) as the live database, and the same chats.
