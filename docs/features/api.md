# The HTTP API

Every feature is an HTTP API first; the browser app, the command line and integrations all use
it (`backend/src/p42cloud/web/`). The installation's `web` service runs it (`p42cloud-web`).

## Routes

| Route | What |
|---|---|
| `GET /api/v1/health` | Whether the app is up (anyone) |
| `GET /api/v1/me` | The caller: their person record, groups, how they're signed in |
| `GET /api/v1/<module>/<table>` | A page of records |
| `POST /api/v1/<module>/<table>` | A new record (201, `Location`, `ETag`) |
| `GET /api/v1/<module>/<table>/<id>` | One record (`ETag`) |
| `PATCH /api/v1/<module>/<table>/<id>` | Change fields; needs `If-Match` |
| `DELETE /api/v1/<module>/<table>/<id>` | Soft deletion; needs `If-Match` |
| `POST /api/v1/<module>/<table>/<id>/restore` | Restore |
| `POST /api/v1/people/<id>/invitation` | A one-time link for a person to set a password and a second factor (`{"reset": true}` first ends their sessions and credentials); administrators only |
| `GET /api/v1/meta/<module>/<table>` | The table described for the caller (below) |
| `POST /api/v1/tables` | Make or change one of the administrators' own tables (`tables.md`) |
| `DELETE /api/v1/tables/<name>` | Retire one of them |
| `POST /api/v1/<module>/<table>/<id>/action/<name>` | Run a UI action on a record (`screens.md`) |
| `POST /api/v1/<module>/<table>/action/<name>` | Run it on several (`ids`), a result per record |
| `GET /api/v1/<module>/<table>/aggregate` | Totals (below) |
| `GET /api/v1/dashboards/<name>` | A dashboard for the caller (`screens.md`) |
| `POST /api/v1/mcp` | The MCP server for AI agents, with an API token (`mcp.md`) |
| `GET /api/v1/installation` | The installation's name and language, and whether setup is open (`setup.md`) |
| `POST /api/v1/setup` | The web installer, while open |
| `GET /api/v1/navigation` | The navigator: what the caller may open (`screens.md`) |
| `GET /api/v1/theme` | A theme's colours and look (anyone) |
| `GET /api/v1/search` | Every list the caller may read, searched at once |
| `GET /api/v1/search/scopes` | The lists the caller may search in |
| `POST /api/v1/undo` | Undo what a request did (`{"request": …}`) or one audit entry (`{"entry": …}`) |
| `/api/v1/sign-in/…` | Signing in and out (below) |
| `GET /api/v1/openapi.json` | The API's description |

Every other address is the browser app (`browser-app.md`); unknown addresses under `/api/`
answer `404` as a problem.

The record routes are the records service (`records.md`): the same rules, access and errors for
every table. Records are JSON: exact numbers and times as text, the version as `_etag`, fields
the caller may not read left out. Lists and single records also carry:

- `_display`: the display value of each reference (the referenced record's display field, if
  the caller may read it) and the label of each choice, in the caller's language;
- `_access`: whether the caller may `write`, `delete` and `restore` this record;
- `_actions`: the UI actions the caller may run on it now.

`fields=a,b` keeps only those fields (and `sys_id`, `_etag`, `_display`, `_access`).

**Describing a table** (`/api/v1/meta/<module>/<table>`), for anyone who may read it: its labels
in the caller's language, its display field, whether it's personal, which operations the caller
may use (on some records), and every field (system fields too) with its type, label, help,
options (choices with their labels), whether it's required or unique, its default, whether it
can be filtered (and its kind: which conditions apply), and whether the caller may read, set or
change it; and `referenced_by`, the tables pointing to it through fields the caller may read.

**Totals** (`/aggregate`) take the same filters as lists, a `measure` (`count`, or `sum`, `avg`,
`min`, `max` of a number `field`), and optionally a `group` (a choice, reference, yes/no, text
or date field; dates per `period`: `day`, `week`, `month`, `year`) with a `limit` of groups (1
to 50). They answer the `total`, the `groups` (key, label, value) and `other` (what the groups
past the limit add up to). Only records the caller may read count, and a field they may not read
counts as empty.

**Lists** take `limit` (1 to 500, default 50), `after` (the `next` of the previous page), `sort`
(`-price,name`), `count`, `deleted`, and filters in two forms, both ANDed:

- `filter[state][in]=open,waiting&filter[assigned_to]=@me` for simple filters (the operator
  defaults to `eq`; lists comma-separated);
- `where=<JSON in the filter language>` for anything with OR or NOT.

## Who may call what

Every route declares it, and a test refuses a route that doesn't: **public** (health, signing in)
or **signed in**. What a signed-in caller may do with records is decided by their grants.

- **Scripts, integrations, agents**: `Authorization: Bearer <API token>` (`api-tokens.md`).
- **The browser**: the session cookie (`p42_session`: HttpOnly, SameSite=Lax, Secure except on
  plain-http development). Every change made with it also needs the header `X-CSRF-Token`, with
  the value of the readable cookie `p42_csrf` (set when signing in), and, when the browser sends
  one, an `Origin` of this installation (`P42_PUBLIC_URL`).

## Signing in

| Route | What |
|---|---|
| `POST /api/v1/sign-in` | Email and password; answers the next stage: `second_factor` or `enroll` |
| `POST /api/v1/sign-in/invitation` | An invitation's token and a new password; then the second factor is set up |
| `POST /api/v1/sign-in/code` | An authenticator or recovery code: signed in (`active`) |
| `POST /api/v1/sign-in/passkey/challenge`, `POST /api/v1/sign-in/passkey` | With a passkey |
| `POST /api/v1/sign-in/setup/app`, `…/setup/app/confirm` | A first authenticator app; its first code signs in and returns the recovery codes |
| `POST /api/v1/sign-in/setup/passkey/options`, `…/setup/passkey` | A first passkey |
| `POST /api/v1/sign-in/verify` | The second factor again (access changes need it recent) |
| `POST /api/v1/sign-in/out` | Signing out |
| `GET /api/v1/sign-in/providers` | The sign-in providers to offer |
| `GET /api/v1/sign-in/provider/<id>` | Off to a provider; it sends the browser back to `…/sign-in/provider-callback` |

## Problems

Errors are RFC 9457 problem details (`application/problem+json`): `type`, `title`, `status`,
`code`, and `errors` per field where it applies. `code` is stable and meant for programs; `title`
and the messages are for people. Unexpected errors say only that something went wrong; the
details go to the log.

## Limits and protection

- **Rate limits**: `P42_REQUESTS_PER_MINUTE` (default 600) per person, API token and network
  address; more are refused (429, `Retry-After`) for the rest of the minute. Counted per process.
- Every answer carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer` and a `Content-Security-Policy` that allows no framing; API
  answers aren't cached.
- **Behind a reverse proxy** (which does TLS): set `P42_TRUSTED_PROXIES` to its address, so its
  `X-Forwarded-For` is believed and limits count per real client. Without it, the address the
  request comes from counts.

## At start

The app refuses to start on a database that isn't safe (`p42cloud check`), then brings the
database, the core and the installed modules to their versions. The installer starts it on the
first free local port from 8080 and waits until it answers.

## The contract

`docs/api/openapi.json` is the API's description. A test fails when the API differs from it:
`uv run python -m p42cloud.web.openapi` writes it again, and the difference shows in review.
