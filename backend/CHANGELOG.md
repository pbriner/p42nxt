# Changelog

The core (`sys`): the engine and the platform's own features.

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
