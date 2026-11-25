# Settings

An installation's settings are records (`sys.setting`), changed in the app, not in files
(`backend/src/p42cloud/data/settings.py`). Settings needed before the app starts (the database,
the master key, the address) stay `P42_*` environment variables.

## Declaring settings

A module declares its settings in its manifest:

```python
Setting("task.reminder_days", "Remind after", 3, check=whole_days,
        help="Days without change before a task's assignee is reminded.")
```

- Installing or upgrading writes them with their default. A value someone changed is never
  overwritten; a setting the module no longer declares is retired.
- **Only the value changes**, and every new value goes through the setting's check. Settings
  aren't made or deleted by hand.
- **Public** settings may be read by everyone (the time zone); the others by administrators.

## The core's settings

The address, the release address, the request limit and the session times were environment
variables before: they're settings now, changed in the app (Platform → Settings) and checked as
before. Each process reads them at start and every 30 seconds, so a change takes effect within half
a minute. A value an installation had in its environment becomes the setting on the upgrade that
brings it; while one of these settings is empty, an environment variable still counts (development
and tests set `P42_PUBLIC_URL` and the others there).

| Setting | Default | What |
|---|---|---|
| `sys.time_zone` | `UTC` | Where the installation's days begin: `@today`, `@start_of_week` and dates compared with times in filters. Any IANA time zone, like `Europe/Zurich`. Public |
| `sys.app_name`, `sys.language` | `P42cloud`, `en` | The installation's name, and the language for browsers asking for one the app doesn't speak (set by the web installer). Public |
| `sys.theme`, `sys.stage` | `violet`, `production` | The default theme, and what the installation is for (`screens.md`). Public |
| `sys.public_url` | empty | **The app's address**, like `https://p42.example.com` (scheme, host, port; nothing after; https in production except `localhost`). Passkeys, sign-in providers and links in messages need it |
| `sys.registry_url` | the image's | **Release address**: where signed releases are published (`releases.md`); a released image brings its repository's, a local build none; empty, the installation never asks. https in production |
| `sys.requests_per_minute` | `600` | Per person, token or address (10 to 100,000) |
| `sys.session_idle_minutes`, `sys.session_max_hours` | `30`, `12` | When a session ends: after this long without use (5 to 1440 minutes), and in any case (1 to 720 hours) |
| `sys.passwords_off_for` | none | Kinds of person (`employee`, `external`) who may sign in only through a sign-in provider. People with emergency access keep their password |
