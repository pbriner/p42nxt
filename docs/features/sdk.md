# The SDK

`p42cloud.sdk` is everything a module may use of the platform, and the only part of it a module
may import. The platform's tests read every module's source and refuse any other import of the
platform, any import of another module, and loading code by name (`importlib`, `__import__`).

## What it holds

| Name | For |
|---|---|
| `Module`, `Migration` | The manifest and data migrations (`modules.md`) |
| `Table` and the field types (`String`, `Decimal`, `Reference` …) | Declaring tables (`tables.md`) |
| `Role`, `Grant` | Declaring roles (`access.md`) |
| `Section`, `Item`, `ListView` | Declaring navigation and list views (`screens.md`) |
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

- `list`, `get`, `create`, `update`, `delete`, `restore`.
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
