# The SDK

`p42cloud.sdk` is everything a module may use of the platform, and the only part of it a module
may import. The platform's tests read every module's source and refuse any other import of the
platform, any import of another module, and loading code by name (`importlib`, `__import__`).

**The SDK only grows** within a major version of the core, so a module keeps working on every
later core it accepts (`sys >=0.38,<1`). Nothing is taken away, renamed or moved, no parameter
becomes required and no new one is required. `backend/tests/sdk_surface.json` records every
name and the parameters of everything callable; a test refuses a change that breaks it, and asks
for additions to be recorded (`P42_SDK_SURFACE=write uv run pytest tests/test_sdk_surface.py`).

## What it holds

| Name | For |
|---|---|
| `Module`, `Migration` | The manifest and data migrations (`modules.md`) |
| `Table` and the field types (`String`, `Decimal`, `Money`, `Currency`, `Reference`, `Number`, `Formula`, `Rollup` …) | Declaring tables (`tables.md`); `Client.convert()` converts amounts (`currencies.md`) |
| `Role`, `Grant` | Declaring roles (`access.md`) |
| `Section`, `Item`, `ListView`, `FormView`, `FormSection`, `RelatedList` | Declaring navigation, list views and forms (`screens.md`) |
| `Job`, `Schedule`, `JobRun` | Declaring jobs and when they run (`jobs.md`) |
| `ConnectionType` | Declaring an outside system; `Client.connection()` gives a ready client (`connections.md`) |
| `NotificationKind` | Declaring what a module tells people about; `Client.notify()` sends (`notifications.md`) |
| `OutsideCall`, `CallRun`, `CallRefused`, `GaveUp` | Declaring a change in an outside system; `Client.call()` records one (`calls.md`) |
| `EventType`, `Subscription`, `EventRun` | Declaring what a module tells other modules about, and what it does when another publishes; `Client.publish()` publishes (`events.md`) |
| `Setting`, `Invalid` | Declaring a module's settings (a default and a check); `Client.setting(name)` reads a public one, or any the caller may read (`settings.md`) |
| `Records` | The type of what hooks, jobs and calls get, for type hints; modules use it only through `Client` |
| `UIAction`, `ActionInput`, `ActionRun`, `ActionResult`, `FormRule`, `Widget` | Declaring buttons, form rules and widgets (`screens.md`); `Client.action()` runs an action |
| `TableHook`, `Change` | Code to run when the module's own records change (`records.md`). `change.person` is who makes the change |
| `Client` | Reading and changing any module's records (below) |
| `Problem`, `FieldError` | Refusing something, with what's wrong per field |

## The client

A module reads and changes records, its own and other modules', only through the client: the
same operations, rules and errors as the HTTP API, without the network.

```python
client = Client.of(change)          # in a hook: the same person and the same transaction
found = await client.list("task.task", where={"field": "state", "op": "eq", "value": "open"})
made = await client.create("hello.greeting", {"text": "Hi"})
await client.update("hello.greeting", made["sys_id"], {"text": "Hello"}, made["_etag"])
```

- `list`, `get`, `create`, `update`, `delete`, `restore`. `list(columns=["partner.city"])` adds
  fields of referenced records.
- `approvals(table, id)`, `decide(approval, decision, note=)` and `pending_approval(table, id)`
  read and decide approvals (`approvals.md`).
- `comment(table, id, text, kind="comment")` adds a comment, or a work note with
  `kind="work_note"`; `comments(table, id)` reads them (`comments.md`).
- Records come and go as JSON: exact numbers and times as text, the version as `_etag`, fields
  the person may not read left out.
- **Whose rights**: the person making the request. A module never shows anyone data they
  couldn't fetch themselves.

## Testing a module

`p42cloud.sdk.testing` is a pytest plugin. In the module's `tests/conftest.py`:

```python
pytest_plugins = ["p42cloud.sdk.testing"]

@pytest.fixture(scope="session")
def p42_modules() -> list[Module]:
    return [module]
```

Each test session gets its own database, set up the way the installer does it, with the core and
these modules installed. The fixtures `as_system()` and `as_person(id)` give a client for one
request, committed at the end. `as_person` acts with the rights the person's groups give them,
as if they had just given their second factor.

```bash
cd modules/hello && uv run pytest
```

## The workspace

The repository is one uv workspace: the platform (`backend/`) and every module
(`modules/<id>/`), one lock file at the root, every package editable. `uv sync --all-packages`
at the root gives an environment for all of them. The app image holds the platform only.

`run_job("<module>.<job>")` runs one of the module's jobs at once, as its service account,
like the worker (`jobs.md`). `call_route(person, method, address)` calls a module route as a
person (`module-routes.md`). `deliver_calls()` sends the outside calls that are due, as the worker would (`later=True` as if
their waits were over); `deliver_events()` hands out the events that are due, the same way. `stand_in("<module>.<type>", answer)` answers a connection type's
requests with the test's own function instead of the outside system (an `httpx` handler), with
secrets sealed by a key made for the test.
