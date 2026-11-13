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

| Setting | Default | What |
|---|---|---|
| `sys.time_zone` | `UTC` | Where the installation's days begin: `@today`, `@start_of_week` and dates compared with times in filters. Any IANA time zone, like `Europe/Zurich`. Public |
| `sys.passwords_off_for` | none | Kinds of person (`employee`, `external`) who may sign in only through a sign-in provider. People with emergency access keep their password |
