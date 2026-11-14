# Changelog

The core (`sys`): the engine and the platform's own features.

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
