# Backups

The core backs up the database every night: the job `sys.backup` (schedule "Database backup,
daily", 02:30 UTC) dumps the whole database with `pg_dump` into the installation's backup
folder.

## Where and when

| What | Where it's set |
|---|---|
| The folder | The setting **Backup folder** (`sys.backup_folder`): Platform → Settings. The folder on the machine; the installer sets it (`~/Backups/p42cloud-<name>` by default). A change applies on the machine with `deploy/restart.sh <name>` (or the next `deploy/update.sh`) |
| When | The schedule "Database backup, daily": Platform → Automation → **Schedules**, its time (cron, time zone) changed like any schedule's, or **Run now** |
| How long they're kept | The setting **Keep backups (days)** (`sys.backup_days`, 30 by default): older daily backups go after each new one |

**Platform → Automation → Backups** lists the backups in the folder (file, folder, when, size,
daily or before an update), as the job last saw it: after each backup, files that are gone are
taken off the list. The backups stay files on the machine; the app doesn't hand them out.

Each daily backup is one file, `p42-<YYYYMMDD-HHMMSS>.dump` (UTC), in PostgreSQL's custom format,
readable only by its owner. It's written under a temporary name and renamed once complete, so a
file with a real name is always a whole backup. Only files named this way are ever removed;
`deploy/update.sh` makes `p42-before-update-<time>.dump` before each update, and those are kept.

The folder must be a folder of its own: a full path, not one of the machine's own folders (like
`/etc` or `/Users`), not a temporary folder (the machine empties those).

A backup that fails is a failed job run: administrators are notified (`jobs.md`). In
production, a backup folder that isn't set, or set but not given to the worker yet, fails the job
too, so a missing backup is never silent. A folder changed but not applied yet: the job backs up
into the folder it has, and says the change is waiting.

### How the folder reaches the worker

The worker runs in a container and can only write where it's given a folder. The deploy scripts
read the setting from the database and give the worker that folder (Docker's configuration needs
it before anything runs, so they keep a copy as `P42_BACKUPS` in `.env`; change it in the app,
not there). The worker is told which folder it was given, so the job knows when a change waits.

## What's in it, and what isn't

Everything in the database: records, settings, the audit trail, sealed secrets. **Not the master
key** (`~/.p42cloud/<name>/master.key`): keep a copy of it apart from the backups (a password
manager). Without it a backup's secrets can't be opened; with both in one place, whoever has the
backups has the secrets.

The backup folder is on the same machine: copy it elsewhere too (another disk, a NAS, cloud
storage).

## Restoring

This replaces the installation's database with the backup's, as a whole:

```bash
cd ~/.p42cloud/<name>
docker compose stop web worker
docker compose cp <backup folder>/p42-<time>.dump postgres:/tmp/restore.dump
docker compose exec postgres pg_restore -U p42_admin -d postgres --clean --create \
  --exit-on-error /tmp/restore.dump
docker compose exec postgres rm /tmp/restore.dump
docker compose start web worker
```

`--create` rebuilds the database with its permissions.

Into a **new installation** (another machine, a test copy): database roles belong to the database
server, not to the backup, so first make each installed module's role there (as `p42_admin`, for
each module besides the core, here `dca`):

```sql
CREATE ROLE p42_mod_dca NOLOGIN NOINHERIT;
GRANT p42_mod_dca TO p42_app WITH INHERIT FALSE, SET TRUE;
GRANT p42_mod_dca TO p42_owner WITH ADMIN OPTION, INHERIT FALSE, SET TRUE;
```

then restore as above, set the setting **Backup folder** to the new installation's own folder (the
backup carries the old one's), and install the modules' packages again (`module install`). Restoring over the existing tables
instead (`--clean` alone) doesn't work: the audit trail's monthly parts can't be dropped that
way.

## Who writes the folder

The app and the worker run as the person who installed the installation (`P42_UID`, `P42_GID`
in `.env`, written by `install.sh`), so the backup folder and the master key stay theirs alone
and need no other owner. An installation made before this runs as the image's own user (10001);
on Linux its backup folder must be writable for that user (`sudo chown 10001 <backup folder>`).
