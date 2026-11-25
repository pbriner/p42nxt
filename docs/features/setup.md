# Setting up an installation

## Installing

```bash
deploy/install.sh <name> [--image p42cloud:<version>] [--backups <folder>] [--qa]
```

It makes the installation's folder (`~/.p42cloud/<name>`: settings with random passwords, the
one-time setup code, the master key, readable only by you) and its backup folder (`--backups`, or
`~/Backups/p42cloud-<name>`); the app runs as
you, so it can read them, starts the database, sets it up,
brings it to this version, checks that it's safe and starts the app on the first free port
from 8080. It refuses to touch an installation that exists already, or a name Docker already
knows. At the end it prints the app's address and the setup code.

Keep a copy of `master.key` somewhere safe: without it, encrypted secrets can't be recovered.
The database is backed up every night into the backup folder, a setting in the app (Platform →
Settings, **Backup folder**; `backups.md`).

To apply a setting that needs the machine (the backup folder) after changing it in the app:

```bash
deploy/restart.sh <name>
```

Modules beyond the core are installed from signed packages into the volume `modules`
(`releases.md`). New releases and modules come from the **Release address** (Platform →
Settings; for releases on GitHub: `https://github.com/<owner>/<repo>/releases/download/registry`).
A released image brings the address of the repository it was built from, so a new installation
starts with it; an image built on your own machine brings none. Empty: the installation never
asks.

## Updating

From the app: set up the updater once (`deploy/updater-install.sh <name>`), then administrators
use **Update now** or **Update tonight** under Platform → Available updates (`releases.md`). By
hand:

```bash
deploy/update.sh <name> [--version <version> | --image <image>]
```

Backs up the database, moves the installation to the new core (checked against the signed
registry), upgrades the database and starts it again; the version before comes back if the
upgrade fails (`releases.md`).

## The web installer

Open the app: while nobody administers the installation, the sign-in page leads to `/setup`.

1. **Setup code**: the one the installer printed.
2. **Your company**: the language, and the installation's name (in the navigator, on the
   sign-in page and in the browser tab).
3. **The first administrator**: name, email, password (12 characters or more). They keep a
   password even if a sign-in provider is set up later (emergency access).
4. **Modules**: the installed modules; the core's are always included, and a module's
   dependencies stay with it. The others can be switched off (and on again later). The
   **Release address** the image brought, to keep, change or clear.
5. **Safety**: the **Backup folder** install.sh made, to keep or change (a new one applies once
   `deploy/restart.sh <name>` has run on the machine); backups are set up, or knowingly
   postponed; a copy of the master key is stored safely (required).

Right after setting up, the update check runs (not at its daily time), so the registry's modules
are under Platform → Available updates within a minute or so.

The administrator is then signed in up to the second factor and sets one up: an authenticator
app or a passkey, then the recovery codes.

The installer works only while the installation has no active administrator, and only with the
setup code. Wrong codes are recorded; after 20 from one address within an hour it waits.
Without a setup code (`P42_SETUP_CODE`) it stays closed; the first administrator is then made at
the command line:

```bash
docker compose --project-directory ~/.p42cloud/<name> run --rm create-admin --name "…" --email …
```

| Route | What |
|---|---|
| `GET /api/v1/installation` | The installation's name and language, and whether setup is open (anyone) |
| `GET /api/v1/setup/modules` | While open: the installed modules |
| `GET /api/v1/setup/settings` | While open: the backup folder and the release address |
| `POST /api/v1/setup` | While open, with the code: sets the installation up (optionally with `backup_folder` and `release_address`) |
