# Module routes

A module adds its own HTTP routes only for what the generic record routes can't express: a
calculation, a summary, a source widget's figure.

```python
async def profit(request: RouteRequest) -> dict[str, object]:
    plans = await Client.of(request).list("dca.plan", where={...})
    return {"value": ..., "unit": "CHF"}

routes=(Route("GET", "/widget/profit", profit, "Profit or loss of the caller's plans."),)
```

| Part | Meaning |
|---|---|
| `method` | `GET`, `POST`, `PUT`, `PATCH` or `DELETE` |
| `path` | Under `/api/v1/<module>`: parts of `a-z`, `0-9`, `_` and `-`, or `{parameter}`; at most six |
| `handler` | The module's code: gets a `RouteRequest`, answers JSON |
| `description` | What it does (shown in the API's description) |

- **Signed-in callers only**, with a session or an API token; changes need the CSRF token like
  every other route. There are no public module routes.
- **The caller's grants apply**: the handler reads and changes records with
  `Client.of(request)`, as the caller, in one transaction kept only if the handler succeeds.
- `request.params` holds the path's parameters, `request.query` the query string,
  `request.body` the JSON body (at most 1 MB), `request.person` the caller.
- **Never in the way of the generic routes**: a route's first part can't be one of the module's
  tables, a parameter, or `action`, `aggregate`, `external`, `bulk` or `options`.
- A **switched-off module's** routes answer 404.
- Routes become records of `sys.module_route` (administrators read them), named
  `<module> <METHOD> <path>`.

## Testing

The SDK's test plugin calls a route as a person, without HTTP:

```python
answer = await call_route(person, "GET", "/hello/widget/languages")
```
