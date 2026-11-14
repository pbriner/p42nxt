# The MCP server

AI agents use the installation through the Model Context Protocol, at `POST /api/v1/mcp`
(Streamable HTTP, protocol version `2025-06-18`, one JSON-RPC message per request, answers as
JSON; no event stream).

- **Only with an API token** (`Authorization: Bearer …`, `api-tokens.md`); a browser session is
  refused. A request with an `Origin` from elsewhere is refused.
- **The tools follow the token's grants**: each tool lists, in its input schema, exactly the
  tables the token may use for it; a tool it couldn't use at all isn't offered.
- **The same rules as the API**: the tools run through the same services, with the same access,
  checks and errors. A refusal comes back as a tool result with `isError` and the problem.
- **Audited as the agent**: changes carry the channel `mcp:token:<token name>` (a record's
  `source`, the audit log).

| Tool | What |
|---|---|
| `list_tables` | The tables the token may read, and what it may do with each |
| `describe_table` | A table's fields, display field and the tables pointing to it |
| `list_records`, `get_record` | Records, filtered in the filter language and sorted |
| `create_record`, `update_record`, `delete_record` | Changes; updating and deleting need the record's `_etag` |
| `run_action` | One of a record's UI actions (`screens.md`) |
| `aggregate` | Totals, overall or per group |
| `search` | Every list the token may read, searched at once |

To connect an agent, give it the address `https://<installation>/api/v1/mcp` and an API token
made for it, with only the roles it needs.
