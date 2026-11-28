# Silent mode

An installation that isn't production holds everything that would change something outside it,
so testing with real data never messages people, places orders or switches devices.

## When it's silent

The installation's environment (`P42_ENVIRONMENT`) decides. **Production always delivers.**
Development, test and QA installations are silent unless an administrator turns delivery on with
the setting **Deliver outside on a test installation** (`sys.deliver_outside`, off by default).
The setting has no effect in production. Changing it is audited like any setting, and takes
effect within 30 seconds (when processes next read the settings). It starts with the
environment variable `P42_DELIVER_OUTSIDE` (the tests set it).

If a process can't tell what it is, it holds.

## What's held

| What | On a silent installation |
|---|---|
| Telegram messages | The delivery is recorded as **Held** and never sent; the notification still appears in the app |
| Outside calls (`calls.md`) | Recorded as **Held** and never sent; the module hears that the call wasn't sent (`gave_up` with `not_sent`) |
| Connections a module uses | Any request but reading (`GET`, `HEAD`, `OPTIONS`) raises `Held`; see below for modules that act with GET or read with POST |

Reading goes on: scans, device states, prices and connection checks (**Works**) still reach the
outside, so a test installation behaves realistically. The platform's own calls (sign-in
providers, the update check) aren't held.

Held deliveries and calls are listed with the state **Held** in their lists.

## In a module

`Client.connection()` holds what changes things by its method. Two kinds of systems need to say
more:

```python
# A device switched with GET: hold every request.
async with client.connection("cmdb.shelly", acting=True) as http: ...

# An API that reads with POST (Kraken's private API, the balance): hold nothing; orders go out
# as outside calls, which are held.
async with client.connection("dca.kraken", owner=owner, acting=False) as http: ...
```

`Held` (from `p42cloud.sdk`) is an `httpx.HTTPError`, so code that handles an unreachable system
already copes; a module can catch it first to say so more clearly. The test fixture `silent`
(`sdk.md`) makes a test run as a silent installation.

## The banner

Every page of a test installation says so: "Test installation: messages and outside calls are
held", or, with delivery turned on, that they ARE delivered. The installation info
(`GET /api/v1/installation`) gives `test_installation` and `held`.
