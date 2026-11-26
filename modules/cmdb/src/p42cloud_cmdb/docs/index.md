# CMDB

What the company runs, where it is and what needs what.

## Items and classes

Every item has a **class**: computers, servers, network devices (routers, switches, firewalls,
access points), storage, printers, phones, UPS, IoT and **Shelly** devices, patch panels,
virtual machines, container engines, stacks, containers, images, networks and volumes, database
servers and databases, applications, business applications, certificates and p42 agents. All
share the basics (status, owner, support group, place, manufacturer, serial, warranty) and add
their own. **All items** lists them together; each has a number (CI-0000001).

Your own classes: make a table of your own (Platform → Tables) and register it under CMDB
settings → Classes.

## Relations and maps

Relations link items: a container **runs on** an engine, the engine on a VM, the VM on a server;
an application **uses** a database and is **secured by** a certificate. Managers add their own
kinds. The **map** on the CMDB dashboard shows a chosen item: what it needs to the right, what
needs it to the left; each box opens its item.

## Found by scans

The UniFi console and Proxmox are read every 30 minutes, once an administrator has set up their
connection (for UniFi, the type **UniFi console: devices for the CMDB**). What's known is kept up to date (only
the fields scans fill; what you typed stays); what's new waits in the **Review list**: create
the item (one or several at once; the row then links to it), say it's one you know, or ignore
it. Created items are in **All items** and in their class's list.

To get a task for a group when a scan finds something, an administrator adds a task rule (Tasks):
opens on **Something found waits for review**, closes on **Everything a scan found is reviewed**.
There's then one task per scan, counting its new finds, closed once they're all reviewed.

## Shelly devices

Read every 5 minutes (state, power, energy, temperature); **Switch on**, **Switch off**. One not
answering for 15 minutes is down. In another network, choose the agent that reaches it.

## Certificates

Paste a certificate: its names, issuer and dates are read from it. 30, 14 and 3 days before it
ends, its owner and support group are told.
