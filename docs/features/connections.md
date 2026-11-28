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

In the app, a new connection asks for its type and a name; its address may stay empty for the
type's usual one. Once it's made, its form shows the **keys** its type needs (Kraken: API key and
API secret), each set or not; one typed in is saved as its secret record, sealed at once, and
never shown again.

**Check** (an action on the connection) runs the type's check and records whether it works,
when, and what it said.

**Devices in your own network** (smart plugs, a router, a hypervisor): a connection type marked
`local` reaches nothing on the internet and only the ranges an administrator names on the
connection (**Local ranges**, one per line, like `192.168.1.0/24`: inside a network, at most a
/16), over http or https. Ranges that reach the installation's own machine or the cloud's
metadata address are refused (loopback, `0.0.0.0/8`, link-local, and IPv6 ranges that wrap
IPv4 addresses), and each request is checked again. It's the one exception to the safe client's
rule, explicit on the connection and in the audit log.

Its **address** is an IP address in one of its ranges, or a host name (like
`https://console.home.example.net`, behind your own reverse proxy). A name is looked up on every
request; every address it has must be in the ranges, else the request is refused, and the
request goes to the address just checked, so the name can't point elsewhere in between. The
certificate is then checked against the name as usual. No user or password goes in the address.

A device with its own certificate (a UniFi console, Proxmox) can have it pasted (**Its own
certificate**): only that one is trusted, whatever name or address it's reached by. Certificates
devices make themselves are often loose (one that may sign certificates without saying it's an
authority); they're accepted when pasted, since nothing else is trusted.

A secret may have an **Expires** date (when the outside system stops taking it). The daily job
**Remind owners of expiring secrets** tells the connection's owner two weeks before and again
on the day (notification kind **A connection's secret expires**). A new value or date resets
the reminder.

## Using a connection in a module

```python
async with client.connection("dca.kraken", owner=plan["owner"]) as http:
    answer = await http.post("/0/private/AddOrder", data={...})
```

The client knows the connection's address and signs in with its secrets; the module never sees
a key. A person may use their own connections; a module's jobs may use connections of the
module's own types for the people they work for. Reading calls (`GET`, `HEAD`) are tried again
when the other side is busy (429, 502, 503, 504), waiting as it asks, at most 30 seconds.

On a test installation, requests that change something are held (`acting=` says more when the
method doesn't tell); see [Silent mode](silent-mode.md).

## Changing things in an outside system

To change something in an outside system (place an order, create a ticket), a module records an
outside call instead of calling directly; see [Outside calls](calls.md).

## The module update check

A daily core job (`sys.check_updates`, 05:37 UTC) reads the registry's signed index through the
safe client, only from the registry's own host, and records the updates that fit. Without a
release address (the setting `sys.registry_url`) it does nothing.
