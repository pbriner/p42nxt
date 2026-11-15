# Changelog

The core (`sys`): the engine and the platform's own features.

## 0.31.0

- Settings in the app instead of environment variables: **The app's address**
  (`sys.public_url`), **Release address** (`sys.registry_url`), **Requests per minute**, and the
  session times (**Sign out after inactivity**, **at the latest**), checked as before. Processes
  read them at start and every 30 seconds, so a change takes effect within half a minute without
  a restart. A value an installation had in its environment becomes the setting on this upgrade;
  while a setting is empty, the environment's still counts (development, tests).
- Sign-in providers are refused when used without the app's address, not at start: setting the
  address switches them on.
- The deployment no longer passes `P42_REGISTRY_URL`; the deploy scripts don't need it.

## 0.30.0

- Widgets form one catalog: every dashboard offers every widget the person may read, its own
  dashboard's first and grouped by module, so a module's widgets (Kraken DCA's) can be placed on
  the home dashboard. A widget's `dashboard` is where it's placed by default; `shown` and the
  audiences apply there, elsewhere it's available.
- A widget about a record (a plan) keeps its own choice where the page offers none: the card
  offers the records and saves the choice with its placement (`params`); it can be placed once
  per record.
- Appearance → **Widgets**: the catalog for administrators, with each widget's module, and its
  audiences on its form.

## 0.29.0

- The browser app asks for the authenticator code again when a change needs a recent one
  (access, API tokens, invitations, some actions), then makes the change: before, it only said so,
  and the change couldn't be made in the app at all after the first 15 minutes.

## 0.28.0

- The platform trusts the maintainer's release key: signed releases and packages can be
  installed now (with no key, nothing could).
- `release_tool sign --file <package>` signs a package by hand (asks for the private key, not
  shown), to install it from the file; it refuses a key the platform doesn't trust.

## 0.27.0

- References filled from a key (`Reference(table, key=, match=)`): the database links a record to
  the one its key field names, on every write by any way in; records there already are linked
  when the reference is added. The job and schedule links use it now.
- The core's records that named others by text link them too, so their forms get related lists:
  the module (jobs, schedules, roles, views, forms, UI actions, form rules, navigation, themes,
  widgets, connection and call types, notification kinds, outside calls, routes, documentation,
  available updates), the table (views, forms, UI actions, form rules, navigation), the
  notification kind (notifications, subscriptions), the connection type (connections, call
  types), the call type (outside calls), the list view (saved views, view states, navigation,
  list widgets), the widget (dashboard placements), the form (related-list layouts) and the
  theme (preferences). Lists show the links.

## 0.26.0

- Schedules and job runs link their records: a schedule its job (`job_ref`), a run its job and
  schedule (`job_ref`, `schedule_ref`), beside the names the worker goes by. A job's form lists
  its schedules and runs, a schedule's form its runs, and the lists show the links. Runs and
  schedules from before are linked on the upgrade. A schedule's job can't be changed: it comes
  with its module.
- A form's related lists: the open list is inside its tab's panel (screen readers announced an
  empty panel, with the list after it).
- The navigator: the module of the page shown opens by itself, and can be closed there again.

## 0.25.0

- The backup folder is a setting: **Backup folder** (`sys.backup_folder`), the folder on the
  machine, set by the installer. It must be a full path of its own (not a system or temporary
  folder). The environment no longer chooses it (`P42_BACKUP_DIR` is gone): the deploy scripts
  give the worker the folder the setting names and tell it which one that is
  (`P42_BACKUPS_MOUNTED`); a change waiting for that is said by the job.
- Backups as records (`sys.backup`, Platform → Automation → **Backups**, administrators): each
  backup in the folder, daily or before an update, with its time and size; refreshed after each
  backup.
- `p42cloud backup folder [--set]` reads or sets the setting (for the deploy scripts).
  `deploy/restart.sh <name>` applies it and restarts; `deploy/update.sh` applies it too, and an
  installation from before keeps its folder (recorded once).

## 0.24.0

- Core releases: a tag `v<version>` builds the app image for Intel and ARM machines, pushes it to
  the repository's container registry, and adds it to the signed registry index by its digest
  (`release_tool publish-image`).
- `p42cloud core image [--version]` names the image to update to, checked with the keys the
  running image trusts: in the signed index, signed, by digest, never a lower version.
- `deploy/update.sh <name>`: backup, the new image's own compose file, upgrade, check, start; the
  version before comes back if the upgrade fails. `--image` updates to an image built here.
- The image carries its `compose.yml`; its browser app is built once for all platforms.

## 0.23.0

- Modules from signed packages: `p42cloud module install <module>` (when the installation hasn't
  got it) and `module update <module> [--version]` take the release from the registry,
  `--file` a package downloaded by hand (next to its `.sig`). The package must match the signed
  index and a trusted key's signature, fit what's installed, hold only its own module and need no
  library the image hasn't got. It's unpacked into the modules folder (`P42_MODULES_DIR`), the
  version before put back if the install fails; each install is a security event.
- The app, the worker and `upgrade` find the modules in that folder. The deployment has a
  `modules` volume (read-only except for the new `module` command) and passes `P42_REGISTRY_URL`
  from `.env`.
- Reading the registry follows its redirects (GitHub serves release files from its download
  hosts), each one checked by the guarded client.
- Settings left empty in `.env` count as not set.
- A list's **Add filter** waits for the table's fields: opened earlier, it offered no field.

## 0.22.0

- Backups as a core job: `sys.backup` (daily, 02:30 UTC; changeable like any schedule) dumps the
  whole database with `pg_dump` into the installation's backup folder (`P42_BACKUP_DIR`; the
  worker's `/backups`, the host's `P42_BACKUPS`), named by its time, written whole or not at
  all; backups older than the setting **Keep backups (days)** (30) are removed. In production a
  missing folder fails the job, so administrators hear of it.
- The app image has PostgreSQL 18's `pg_dump` and `pg_restore`.
- A restore procedure, rehearsed: the database recreated whole from a backup.

## 0.21.0

- Widgets about one record the person picks (`Widget(param=("plan", "dca.plan"))`, source
  widgets only): the dashboard offers the table's records above the widgets, keeps the choice in
  the address (the first one by default), and passes it to each source route; without one the
  card says to choose.

## 0.20.0

- Outside calls tell their module when they end without an answer (`OutsideCall(gave_up=...)`,
  `GaveUp`): refused for good, not sent (failed), or unclear after the last try (it stays
  unknown and isn't tried again).
- The SDK: `Setting` and `Invalid` for a module's settings, `Client.setting(name)` reads public
  settings (or any the caller may read), `Records` for type hints; the test plugin's
  `deliver_calls()` sends the outside calls that are due.

## 0.19.0

- A module's own jobs (its service account) see and change the personal records of the module's
  own tables, for everyone they work for (a buy for each plan's owner); nobody else's tables,
  and only within the module's grants.
- The test plugin's `stand_in(type, answer)` answers for an outside system in a module's tests.

## 0.18.0

- Module routes (`Route`, `RouteRequest`, `sys.module_route`): a module's own HTTP routes under
  `/api/v1/<module>/…`, for signed-in callers, mounted before the generic record routes they
  may never hide; the handler works as the caller through `Client.of(request)`. A switched-off
  module's routes answer 404. The test plugin's `call_route` calls one as a person.
- Source widgets: a figure or chart takes its data from a GET route of its module
  (`Widget(source=...)`), for what a list can't count or sum.

## 0.17.0

- Telegram as a notification channel: the installation's bot as a shared connection
  (`sys.telegram`); people link their chat from the profile with a one-time link (only its
  hash is kept, for 15 minutes); `/stop` or Disconnect unlinks, and so does a blocked bot.
- Notification kinds have default channels (`NotificationKind(channels=...)`, administrators may
  change them; upgrades keep their change); each person chooses per kind in the profile.
- Deliveries are queued with the notification and sent by the worker, tried again with growing
  waits or as Telegram asks; only the title unless the person wants the whole message.
- Shared connection types (`ConnectionType(shared=True)`): one per installation, set up by
  administrators.
- Failed schedules are on Telegram by default.

## 0.16.0

- Notifications (`NotificationKind`, `sys.notification_kind`, `sys.notification`): a module
  notifies people and the holders of roles with its change (`Client.notify()`), with a title,
  a message, a link inside the app and a severity. Each person's notifications are theirs alone;
  they mark them read or unread and put them away.
- The bell in the top bar: unread count, the latest ten, mark all as read, show all. It asks
  once a minute without keeping the session alive (`X-P42-Background`).
- Administrators are notified when a schedule starts failing (`sys.job_failed`).
- The daily job `sys.notification_cleanup` puts away old notifications.
- My work → Notifications; Automation → Notification kinds.

## 0.15.0

- Outside calls (`OutsideCall`, `sys.call_type`, `sys.outside_call`): a module records a change
  in an outside system with its own change (`Client.call()`), and the worker makes it later
  through the owner's connection. Made at most once: when an answer is lost, the other side is
  asked by the call's key before anything is sent again. Definite refusals fail the call; the
  module's `done` reacts to the answer as its service account.
- The worker delivers calls in turn with job runs, and treats calls a stopped worker left
  sending as lost.
- In the app: Automation → Outside calls; everyone sees the calls made for them.
- Chart legends: each row is at least 24 pixels high, so it's easy to hit.

## 0.14.0

- One safe client for every outside call: https only, no redirects, hosts resolved first and
  refused if they lead inside a network, optionally only certain hosts. Sign-in providers use it.
- Connection types that modules declare (`ConnectionType`: allowed hosts, how to sign in,
  secrets, a check); each person's connections and secrets as personal records, secrets sealed
  on the way in and never read back. `Client.connection()` gives modules a ready client; a
  module's jobs may use its own types' connections for the people they work for. A Check action.
- The module update check runs daily as a job, through the safe client.
- The master key's sealer is the process's, set by the web app and the worker at start.

## 0.13.1

- Forms: in related lists, references link to their records (the person on a group's
  memberships, the group on a person's), if the person may read them.

## 0.13.0

- Jobs and schedules (`sys.job`, `sys.schedule`, `sys.job_run`) that modules declare (`Job`,
  `Schedule`); cron in a time zone; administrators change or pause schedules, and upgrades keep
  what they set.
- The worker (`p42cloud-worker`, the installation's `worker` service): queues due runs, runs
  each in one transaction with a time limit, tries failed runs again with growing waits, never
  overlaps a schedule's runs, ends runs a stopped worker left behind. Several workers may run.
- A module's jobs act as its own service account (its roles); every change carries the run.
- In the app: Automation → Schedules (Run now, Pause, Resume) and Job runs.
- The core's first job: the nightly check of dynamic groups' members.
- The SDK's test plugin can run a job (`run_job`).
- Forms: each person chooses and orders the related lists in a slushbucket, kept for them per
  form (`sys.related_layout`), with the tab open last; Reset to default.

## 0.12.0

- The web installer (`/setup`): with the setup code, a new installation gets its name and
  language, its first administrator, its modules, and the two safety confirmations; the
  administrator then sets up their second factor. Open only until there's an administrator;
  wrong codes are recorded and slowed down. `install.sh` points to it; `create-admin` stays.
- Settings for the installation's name (shown everywhere) and language.
- The MCP server for AI agents (`POST /api/v1/mcp`): tools generated from the token's grants,
  run with the API's rules, audited on the channel `mcp:token:<name>`.

## 0.11.0

- Totals over a table (`GET …/aggregate`): count, sum, average, smallest or largest, overall or
  per group (dates per day, week, month or year), with the person's access applied.
- Widgets (`sys.widget`) that modules declare (`Widget`): figures with target and trend,
  charts, lists, text; shown per group (`sys.widget_audience`). Dashboards for home and each
  module (`GET /api/v1/dashboards/<name>`), arranged by each person (`sys.dashboard_layout`).
- In the app: dashboards with customizing, a widget picker, and plain SVG charts (bars, donut,
  columns, line) that also show as tables.

## 0.10.0

- UI actions (`sys.ui_action`): buttons and menu entries modules declare, offered per record
  (`_actions`) where the person may do the action's operation and its condition holds; they
  may ask first, ask for inputs or for the second factor again, and either set fields or run
  the module's code. Run one on a record or several, checked again and audited.
- Form rules (`sys.form_rule`): fields hidden, required or read-only while a condition holds,
  applied by the app while typing and enforced by the server on every save.
- The SDK: `UIAction`, `ActionInput`, `ActionRun`, `ActionResult`, `FormRule`, and
  `Client.action()`. Only the core may hook every table.
- The app shows field errors at their fields again (the browser's own checks no longer stop a
  form before ours).

## 0.9.0

- Form views are records (`sys.form_view`): modules declare sections and related lists
  (`FormView`, `FormSection`, `RelatedList`); every other table gets one made from its fields.
- Table descriptions list `referenced_by`: the tables pointing to the table, through fields the
  caller may read.
- Forms in the browser app: an editor per field type, saving only what changed, choosing per
  field when someone saved in between, unsaved changes kept in the tab, delete with undo,
  related lists with counts.
- Lists load once when they open (they loaded twice, which could clear a selection).

## 0.8.0

- Lists in the browser app: filters as chips (with relative dates), right-click menus on values
  and headers, columns chosen and ordered, sort and columns remembered, saved views (one may open
  by default), choosing records and updating them at once, deleting with undo, cards on small
  screens. The list's state is in its address.
- Choices may declare tones (`primary`, `tertiary`, `error`, `muted`) to stand out in lists.
- Table descriptions give each field's kind (which filter operators apply).

## 0.7.0

- The browser app, served by the web app at the same address: signing in (password, provider,
  authenticator code or passkey; setting up the second factor, with recovery codes), invitation
  links, the navigator with favorites and folders, the search across lists, home, a first list,
  and the profile (light or dark, theme, records per page, language). In English and German.
- Pages get a strict Content-Security-Policy; the API keeps its own.
- The core's version also covers the browser app: changing it releases the core.

## 0.6.0

- What the screens show is records: navigation items (module, section, item) and list views
  that modules declare (every table gets a view made from its fields if its module declares
  none), and themes (Violet, the default, and Blue).
- Each person's own choices are personal records, through the normal records API: preferences
  (light or dark, theme, records per page, language), favorites in folders, saved views and the
  columns and sort last used on a list.
- `GET /api/v1/navigation`: what the caller may open. `GET /api/v1/theme`: a theme's colours,
  public so the sign-in page looks like the app. `GET /api/v1/search` and `/search/scopes`:
  every list the caller may read, searched at once.
- Settings for the default theme and the installation's stage (production, QA, test,
  development).
- Modules have a category and an icon, and declare `navigation`, `views` and `themes`.

## 0.5.0

- Administrators build their own tables in the app (`u.<name>`): records and database change
  together, as the owner, by the engine's rules; each table gets a reader and an editor role.
- `GET /api/v1/meta/<module>/<table>` describes a table for the caller: labels in their
  language, fields with types, options and choices, what they may do.
- Records over HTTP carry `_display` (references and choices named) and `_access` (what the
  caller may do with each record); `fields=` keeps only some fields.
- Tables name their display field.

## 0.4.0

- Settings are records (`sys.setting`), declared by modules with a default and a check; values
  people change are kept on upgrade. The first: the installation's time zone (used for today
  and dates in filters) and the kinds of person who may not sign in with a password.
- Invitations and resets: a one-time link (7 days) that administrators pass on, leading to a
  new password and then a second factor; a reset ends the person's sessions and credentials.
- A group can follow a sign-in provider's groups claim; such a group never holds a powerful
  role, and its members aren't kept by hand.

## 0.3.0

- The HTTP API: the generic record routes (list with `filter[...]` and `where`, read, create,
  change with `If-Match`, delete, restore), signing in and out (password, code, passkey,
  setting up a second factor, sign-in providers), `me`, `undo` and `health`; errors as problem
  details; every route declares who may call it.
- The browser's session in an HttpOnly cookie, with a CSRF header and an Origin check for
  changes; rate limits per person, token and address; protective response headers.
- `p42cloud-web` runs the app: the installation's `web` service on a free local port; the app refuses
  an unsafe database and upgrades at start. `P42_TRUSTED_PROXIES` names the reverse proxy.
- Everyone may read all of their own person record.

## 0.2.0

- Releases: every changed package needs a new version and a changelog entry (checked in CI);
  module packages are signed (Ed25519) and published to a registry with a signed index.
- `p42cloud module updates` reads the registry and records the updates that fit what's
  installed, security updates first. `P42_REGISTRY_URL` sets the registry; off by default.

## 0.1.0

- Records and the audit log: system fields, no real deletes, an append-only, hash-chained log.
- Tables from records: field types, declarations, the schema engine, the metadata cache.
- The records service: changes with ETags, the filter language, cursor paging, undo.
- Access: people, groups (also with rules), roles and grants applied in SQL, no escalation,
  personal records and sharing.
- Sign-in: passwords, authenticator apps, passkeys, recovery codes, sessions, sign-in providers
  (OpenID Connect), API tokens and service accounts, the first administrator.
- Modules: manifests, install and upgrade, switching off and removing, data migrations,
  documentation pages, the SDK.
