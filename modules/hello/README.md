# Hello (`hello`)

Version: 0.3.0 · Last updated: 2026-11-13

## Purpose

The reference module: it shows how a module is built on the SDK alone, with a table, a role, a
hook, documentation and its own tests. People leave greetings for everyone to read.

## Depends on

| Module | Versions | What for |
|---|---|---|
| `sys` | `>=0.10,<1` | Always; 0.10 brings UI actions and form rules |

Core dependency: no

## Tables

| Table | What a record is | Main fields | Notes |
|---|---|---|---|
| `hello.greeting` | A greeting | text, language, author, pinned, audience, team | The author is set to whoever leaves it |

## Roles and access

| Role | Can | Record conditions / personal data |
|---|---|---|
| `hello.user` (Greeter) | Read all greetings, leave new ones, change and delete their own | Change and delete: `author = @me` |

## Views, navigation and dashboards

In the navigator under Examples → Hello → Greetings: the list of greetings (text, language,
author, pinned, when) and leaving a new one. The form has two sections. No dashboards.

Actions: Pin (a button; also on several at once), Unpin (in the form's menu), Sign (asks for a
signature, runs the module's code). Form rules: the team shows and is required only for a team
greeting; the author can't be changed.

## Hooks

Before a greeting is saved: its text is trimmed and may not be empty; a new one's author is the
person leaving it.

## Data migrations

None.

## Documentation

`src/p42cloud_hello/docs/`: written into the documentation pages at install.

## Tests

`uv run pytest` in this folder: a fresh installation with the core and this module.
