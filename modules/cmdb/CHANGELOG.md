# Changelog

## 0.1.2

- A **Dashboard** entry in the menu (the dashboard with the map was there, but hard to find).
- The UniFi connection type is called "UniFi console: devices for the CMDB", so it isn't
  mixed up with IPAM's.
- Scans write "last seen" again only after 10 minutes unless something else changed, and Shelly
  readings only when they change: far fewer entries in the audit trail. A Shelly device is
  marked down once it surely hasn't answered for 15 minutes (at most 25).

## 0.1.1

Security fixes.

- A Shelly device's address is an IP address in your own network or a host name, nothing more:
  no port, path or user, and not this machine or the cloud's metadata address.
- CMDB operators switch Shelly devices but can't change their address or agent.
- Agents' answers can be read by CMDB managers only, no longer by every CMDB reader.
- Needs core 0.52.1, with its security fixes.

## 0.1.0

- Configuration items in 24 classes (hardware, virtual, containers, data, software,
  certificates, p42 agents), each its own table with the common fields; the index of all items;
  custom classes from administrators' own tables.
- Relation types as records (shipped and your own), relationships checked (classes, loops),
  maps of what needs what and what breaks if it's down; ports, cables and their path.
- Discovery from UniFi and Proxmox into a review list; Shelly devices read and switched (also
  through agents); certificates read from their PEM and warned of; the installation registering
  itself; agents as items. Needs the core 0.52, Places and Partners.
