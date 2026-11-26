# Changelog

## 0.1.1

Security fix: a zone's DNS server is an IP address in your own network, checked when it's
saved: not this machine, its network links or the cloud's metadata address.

Needs core 0.52.1, which brings the DNS library it uses (0.1.0 couldn't be installed without it).

## 0.1.0

- VLANs, subnets (no overlaps), IP addresses with the next free one, DNS zones and records
  (by hand, UniFi, zone transfer), records linked to their address. Needs the core 0.52,
  Places and CMDB.
