# CMDB (`cmdb`)

Version: 0.1.0 · Last updated: 2026-11-26

## Purpose

The configuration items (CIs) a company runs, from a rack to a container, their relations and
maps of what needs what; devices found by UniFi and Proxmox reviewed before they're added;
Shelly devices read and switched; certificates warned of; the installation registering itself.

## Depends on

| Module | Versions | What for |
|---|---|---|
| `sys` | `>=0.52,<1` | Local connections, map widgets, agents, every-table hooks |
| `places` | `>=0.1,<1` | Where items are |
| `partners` | `>=0.1,<1` | Manufacturers and suppliers |

Core dependency: no

## Tables

| Table | What a record is | Notes |
|---|---|---|
| `cmdb.<class>` | One CI of a class: `computer`, `server`, `network_device`, `mobile_device`, `storage`, `printer`, `ip_phone`, `power_device`, `iot_device`, `shelly_device`, `patch_panel`, `vm`, `container_engine`, `container_stack`, `container`, `container_image`, `container_network`, `container_volume`, `db_instance`, `database`, `application`, `business_application`, `certificate`, `agent` | Each with the common fields (name, status, environment, criticality, owner, support group, place, manufacturer, model, serial, asset tag, supplier, bought on, price, warranty, notes, discovery key, last seen, scanned fields) and its own; work notes |
| `cmdb.ci` | The index: one per CI of any class | Number CI-0000001; kept by the classes (people don't write it) |
| `cmdb.ci_class` | A class: shipped, or an administrator's own table (`u.<name>`) registered | Custom classes are indexed too |
| `cmdb.relation_type` | A kind of relation | Shipped: runs on, hosted on, part of, contains, secured by, uses, depends on, connected to, protected by; managers change them (kept) and add their own; classes checked where set; `needs` or `link` for maps |
| `cmdb.relationship` | From a CI, of a type, to a CI | Once per pair; not to itself; no loops of `needs`; both-ways types once |
| `cmdb.interface`, `cmdb.port`, `cmdb.cable` | A device's interfaces (MAC unique), ports, cables between ports | A port takes one cable, a patch panel's two |
| `cmdb.review` | Something a scan found that no CI matches | Create the item, it's one we know, ignore |

## Roles and access

| Role | Can |
|---|---|
| `cmdb.reader` | Read everything (not agents' answers); includes Places and Partners readers |
| `cmdb.editor` | Change CIs, relationships, interfaces, ports, cables, the review list |
| `cmdb.operator` | Switch Shelly devices (not change their address or agent) |
| `cmdb.manager` | Everything, including relation types, classes and agents' answers |

## Views, navigation and dashboards

Navigation: CMDB (Dashboard, All items, Relationships, Review list), one section per family, Network,
CMDB settings. Dashboard `cmdb`: the map of a chosen item, items by class, certificates ending in
30 days, found to review, not seen for 30 days. Bulk "Move to a place" on every class.

## Workflows, approvals and jobs

| Job (schedule) | What |
|---|---|
| Register this installation (hourly) | The application, its modules and the database as CIs (setting "Register this installation", on) |
| Read the UniFi console (every 30 minutes) | Devices and clients into the review list or their CI; "last seen" is written again only after 10 minutes, unless something else changed |
| Read Proxmox (every 30 minutes) | Nodes, VMs, LXC containers |
| Read Shelly devices (every 5 minutes) | State, power, energy, temperature, written only when they change ("last seen" every 10 minutes); down once it surely hasn't answered for 15 minutes (at most 25); through an agent in other networks |
| Warn of ending certificates (daily) | 30, 14 and 3 days before, and once ended |

Events: `cmdb.device_down`, `cmdb.device_recovered`, `cmdb.certificate_expiring`,
`cmdb.certificate_expired`, `cmdb.review_waiting`. Listens for the core's agent events (agents
become items) and `sys.agent_task_done`.

A Shelly device's address is an IP address in your own network or a host name, nothing more
(no port, path or user, not this machine or the cloud's metadata address).

## API

`GET /api/v1/cmdb/map?ci=&direction=down|up|both&depth=` (the map), `GET /api/v1/cmdb/impact?ci=`
(what needs it), `GET /api/v1/cmdb/path?port=` (the cable path).

## Outside systems

UniFi console (connection type "UniFi console: devices for the CMDB", API key), Proxmox (API
token), Shelly devices: connections to your own network (local ranges; the address an IP address
or a host name; a pinned certificate where the device made its own).
