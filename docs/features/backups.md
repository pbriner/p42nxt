# Backups

The core backs up the database every night: the job `sys.backup` (schedule "Database backup,
daily", 02:30 UTC) dumps the whole database with `pg_dump` into the installation's backup
folder.

## Where and when

| What | Where it's set |
|---|---|
| The folder | `P42_BACKUPS` in the installation's `.env` (`~/.p42cloud/<name>/.env`; by default `~/Backups/p42cloud-<name>`). The worker writes to it as `/backups` (`P42_BACKUP_DIR`). Changing it takes a restart: `docker compose up -d worker` |
| When | The schedule "Database backup, daily": Platform → Automation → **Schedules**, its time (cron, time zone) changed like any schedule's, or **Run now** |
| How long they're kept | The setting **Keep backups (days)** (`sys.backup_days`, 30 by default): older backups go after each new one |

Each backup is one file, `p42-<YYYYMMDD-HHMMSS>.dump` (UTC), in PostgreSQL's custom format,
readable only by its owner. It's written under a temporary name and renamed once complete, so a
file with a real name is always a whole backup. Only files named this way are ever removed.

A backup that fails is a failed job run: administrators are notified (`jobs.md`). In
production, a worker without a backup folder fails the job too, so a missing backup is never
silent.

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
docker compose cp ~/Backups/p42cloud-<name>/p42-<time>.dump postgres:/tmp/restore.dump
docker compose exec postgres pg_restore -U p42_admin -d postgres --clean --create \
  --exit-on-error /tmp/restore.dump
docker compose exec postgres rm /tmp/restore.dump
docker compose start web worker
```

`--create` rebuilds the database with its permissions. Restoring over the existing tables
instead (`--clean` alone) doesn't work: the audit trail's monthly parts can't be dropped that
way.

## On Linux

The worker runs as user 10001: the backup folder must be writable for it
(`sudo chown 10001 ~/Backups/p42cloud-<name>`). On macOS, Docker Desktop takes care of that.
