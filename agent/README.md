# The p42 agent

A small program that works for a P42cloud installation from another network: through the
installation's public API, outbound only, with its own service account and token.

1. In the app, an administrator makes a one-time code (`POST /api/v1/agents/codes`, 24 hours).
2. On a machine in the other network (a VM on Proxmox, a Raspberry Pi):
   `python p42agent.py enrol https://p42.example.com <code>`, then run `python p42agent.py run`
   as a service (it asks for its tasks every minute; `P42AGENT_EVERY` changes that).
3. The installation shows it as an agent (and the CMDB as an item); modules hand it tasks.

Tasks it knows today: a Shelly device's state, switching one. It needs Python 3.12+ and httpx.
