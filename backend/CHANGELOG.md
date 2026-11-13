# Changelog

The core (`sys`): the engine and the platform's own features.

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
