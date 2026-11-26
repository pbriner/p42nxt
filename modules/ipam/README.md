# IPAM (`ipam`)

Version: 0.1.0 · Last updated: 2026-11-26

## Purpose

VLANs, subnets, IP addresses (with the next free one) and DNS zones and records, kept by hand,
read from a UniFi console or read from any DNS server by zone transfer.

## Depends on

| Module | Versions | What for |
|---|---|---|
| `sys` | `>=0.52,<1` | Local connections |
| `places` | `>=0.1,<1` | Sites and buildings of VLANs and subnets |
| `cmdb` | `>=0.1,<1` | Items using addresses |

Core dependency: no

## Tables

| Table | What a record is | Notes |
|---|---|---|
| `ipam.vlan` | A VLAN | number, name, site |
| `ipam.subnet` | A network | CIDR (no overlaps), VLAN, place, gateway, DHCP range (inside it) |
| `ipam.address` | An IP address | Its subnet worked out; kind; used by; DNS name |
| `ipam.dns_zone` | A DNS zone | Kept by hand, UniFi or zone transfer (a DNS server in your network) |
| `ipam.dns_record` | A record | A, AAAA, CNAME, MX, TXT, SRV, CAA, NS, PTR; A/AAAA linked to its address |

## Roles and access

`ipam.reader` (read; includes Places reader), `ipam.manager` (everything).

## Views, navigation and dashboards

Navigation (IPAM): Dashboard, Subnets, IP addresses, VLANs, DNS zones.

## Workflows, approvals and jobs

Read DNS zones (daily); Reserve the next free address (a subnet's button).

## API

`GET /api/v1/ipam/next_free?subnet=`.

## Outside systems

UniFi console (connection type "UniFi console: DNS records for IPAM", API key, local
connection); DNS servers by zone transfer (AXFR, without a key
for now). A zone's DNS server is an IP address in your own network, checked when it's saved.
