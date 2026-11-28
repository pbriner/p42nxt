# Changelog

## 0.1.5

- On a test installation, switching a Shelly plug is held and says so, instead of switching the
  real plug. Needs core 0.54.

## 0.1.4

- **Review tasks**: "Something found waits for review" is now about the scan that found it, and
  a new event "Everything a scan found is reviewed" follows when its last waiting find is
  created, attached or ignored. A task rule (Tasks 0.2.1) on these keeps one task per scan for
  the group you choose, counts each new find on it, and closes it once all are reviewed.
- CMDB readers see the CMDB's own jobs (what a find and its task are about).

## 0.1.3

- A review row's **State** is changed only by its buttons. Setting it to "Created" by hand made
  nothing; rows like that wait again after the upgrade, so their items can be created.
- **Create the item** links the review row to the item it made (rows made before are linked by
  the upgrade), and works for several selected rows at once.
- A review row's **Proposed class** is picked from the CMDB's classes, no longer typed.
- Review rows show their **IP address**, **MAC address** and **Host name** (from what the scan
  found), and the review list's search finds them. The upgrade fills them for rows already there.

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
