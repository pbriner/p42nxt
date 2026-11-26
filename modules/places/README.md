# Places (`places`)

Version: 0.1.0 · Last updated: 2026-11-25

## Purpose

Where things are: sites, buildings, floors, rooms and racks as one tree, for every module that
needs a location (CMDB devices, rentals, tasks).

## Depends on

| Module | Versions | What for |
|---|---|---|
| `sys` | `>=0.51,<1` | Hierarchies, references, widgets |

Core dependency: no

## Tables

| Table | What a record is | Main fields | Notes |
|---|---|---|---|
| `places.place` | A site, building, floor, room, rack or other place | name, kind, parent, code, address, responsible, units, notes | A hierarchy (`parent`); nesting checked (a site holds buildings, a building floors or rooms, a floor rooms, a room racks; other anywhere); racks have a height in units (default 42, at most 60); a place with places under it isn't deleted |

## Roles and access

| Role | Can | Record conditions / personal data |
|---|---|---|
| `places.reader` | Read every place | |
| `places.manager` | Everything on places | Includes `places.reader` |

## Views, navigation and dashboards

Navigation (Places): Dashboard, Places, Sites, Racks, New place. A place's form lists the places in it.
Dashboard `places`: places by kind.

## Workflows, approvals and jobs

None.

## API

None beyond the generic records API.

## Outside systems

None.
