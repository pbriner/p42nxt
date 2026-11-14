# Connections and outside calls

## The safe client

Every call the platform makes to another system goes through one client (`safe_client`):

- **https only**, and **no redirects**;
- every host is **resolved first and refused** if it is, or leads to, an address inside a
  network: private, loopback, link-local (the cloud's metadata service), multicast, reserved or
  unspecified, IPv4 and IPv6;
- optionally **only certain hosts** (a connection type's).

Sign-in providers' discovery, keys and tokens, connections and the module update check all use
it.

## Connection types

A module declares the outside systems it talks to:

```python
connection_types=(ConnectionType(
    "kraken", "Kraken", allowed_hosts=("api.kraken.com",), auth="signed",
    secrets=("api_key", "api_secret"), base_url="https://api.kraken.com",
    sign=sign_request, check=check_balance,
),),
```

| Part | Meaning |
|---|---|
| `allowed_hosts` | The only hosts its connections reach (`api.example.com`, or `*.example.com` for its subdomains) |
| `auth` | `none`, `bearer` (secret `token`), `basic` (`username`, `password`), `api_key` (`key`, in `api_key_header`), or `signed` (the module's `sign` adds headers from the secrets to each request) |
| `secrets` | The names of the secrets a connection keeps |
| `base_url` | The usual address (https, on an allowed host) |
| `check` | Whether a connection works: given a ready client, says what it found |

They become records of `sys.connection_type`.

A **shared** type (`shared=True`) has one connection for the whole installation, not one per
person: a bot, a company system. Only administrators (who may change settings) set it up, there
can be only one, and only the type's module (and its owner, to check it) uses it.

## Connections

A connection is a person's own record of `sys.connection` (a type, a name, an address on the
type's hosts); nobody else sees it. Its secrets are records of `sys.connection_secret`: written
through the normal record routes, **sealed at once** with the master key (bound to that
connection and secret, so a sealed value can't be moved to another), never read back, masked in
the audit log. Only the connection's owner sets its secrets, and only the ones its type names.

**Check** (an action on the connection) runs the type's check and records whether it works,
when, and what it said.

## Using a connection in a module

```python
async with client.connection("dca.kraken", owner=plan["owner"]) as http:
    answer = await http.post("/0/private/AddOrder", data={...})
```

The client knows the connection's address and signs in with its secrets; the module never sees
a key. A person may use their own connections; a module's jobs may use connections of the
module's own types for the people they work for. Reading calls (`GET`, `HEAD`) are tried again
when the other side is busy (429, 502, 503, 504), waiting as it asks, at most 30 seconds.

## Changing things in an outside system

To change something in an outside system (place an order, create a ticket), a module records an
outside call instead of calling directly; see [Outside calls](calls.md).

## The module update check

A daily core job (`sys.check_updates`, 05:37 UTC) reads the registry's signed index through the
safe client, only from the registry's own host, and records the updates that fit. Without a
registry (`P42_REGISTRY_URL`) it does nothing.
