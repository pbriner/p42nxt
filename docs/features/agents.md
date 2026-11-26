# Agents

Small programs in other networks that work for the installation: read devices there, scan a
subnet, switch a plug. They talk to the installation's public API only, and only outbound, so
nothing in that network has to be opened (`backend/src/p42cloud/services/agents/`).

## Enrolling one

1. An administrator makes a one-time code for the agent's name (`POST /api/v1/agents/codes`,
   with the second factor given in the last 15 minutes); it works for 24 hours, once.
2. The agent enrols with it (`POST /api/v1/agents/enrol` with the code, its version and host):
   it gets its **own service account** and an **API token** (a year), shown once. What it may
   do is what's given to that account.

The agent is a record of `sys.agent` (name, the account it acts as, version, host, seen from,
last seen); an administrator switches one off (`active`). Switched off, its account and token
stop working; switched on again, they work again. Enrolling an agent again under the same name
stops the old account and its token.

## Tasks

Modules hand out tasks (`Client.agent_task(agent, module, kind, payload)`). The agent asks for
its waiting tasks (`GET /api/v1/agents/tasks`, which also counts as being seen), does them and
answers (`POST /api/v1/agents/tasks/<id>` with `ok` and a `result`, at most 256 kB). The module hears of it as
the event `sys.agent_task_done` (with `module` and `kind`) and reads the answer from the task
(`sys.agent_task`). A task that's still waiting isn't queued a second time, and one not answered
within an hour is marked failed.

An agent not seen for 15 minutes is quiet (`sys.agent_quiet`, once); when it asks again,
`sys.agent_back`. Enrolments, refused codes and new codes are security events.

## The reference agent

`agent/p42agent.py` (Python and httpx): `enrol <url> <code>`, then `run` as a service. It knows
the tasks `shelly_status` and `shelly_switch` today. It talks to the installation over https
only, keeps its token readable by its own user only, reaches devices only by an address in its
own network or a host name (`P42AGENT_RANGES` narrows that to some ranges), and sends back only
the values the installation reads.
