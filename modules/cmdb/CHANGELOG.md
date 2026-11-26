# Changelog

## 0.1.0

- Configuration items in 24 classes (hardware, virtual, containers, data, software,
  certificates, p42 agents), each its own table with the common fields; the index of all items;
  custom classes from administrators' own tables.
- Relation types as records (shipped and your own), relationships checked (classes, loops),
  maps of what needs what and what breaks if it's down; ports, cables and their path.
- Discovery from UniFi and Proxmox into a review list; Shelly devices read and switched (also
  through agents); certificates read from their PEM and warned of; the installation registering
  itself; agents as items. Needs the core 0.52, Places and Partners.
