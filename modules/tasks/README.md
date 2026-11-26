# Tasks (`tasks`)

Version: 0.2.0 · Last updated: 2026-11-24

## Purpose

Work for groups, which their members take themselves: tasks about any record, with a priority
from impact and urgency, opened by hand or by rules on events, and closed again when the matching
event comes.

## Depends on

| Module | Versions | What for |
|---|---|---|
| `sys` | `>=0.47,<1` | States, events, comments and work notes, links to any record, record numbers |

Core dependency: no

## Tables

| Table | What a record is | Main fields | Notes |
|---|---|---|---|
| `tasks.task` | A piece of work for a group | number (TASK-0000001), title, description, state, impact, urgency, priority, group, taken_by, due, about_table, about, closed, rule, repeats, last_seen, told | A state flow; comments and work notes. Priority, taken_by, closed, rule, repeats, last_seen and told are filled by the module and refused when sent. The group must be one work is assigned to (`sys.group.assignable`); it can't be deleted while tasks point to it |
| `tasks.watcher` | A person watching a task | task, person (default: who adds it) | Unique per task and person; deleted with its task |
| `tasks.rule` | Turns an event into a task | name, active, event, resolved_by, title, group, impact, urgency, due_days | Event and resolved_by are choices of the events the module listens for |

## Roles and access

| Role | Can | Record conditions / personal data |
|---|---|---|
| `tasks.user` | Read every task, watcher and rule; make tasks for any group; change its groups' tasks; change or cancel the tasks it made while they're new; add and remove itself as a watcher; read groups' name, description and whether they take work | Writing tasks: `group in @my_groups`, or `created_by = @me` while new. Watchers: `person = @me` |
| `tasks.manager` | Everything on the module's tables, and who is in which group (`sys.membership`: group, person) | Includes `tasks.user` |

The module's jobs and event handlers act as its service account, which holds both roles.

## Views, navigation and dashboards

- Navigation (Work → Tasks): Dashboard, My work (`taken_by = @me`, in progress or waiting), My
  groups' work (`group in @my_groups`, open), All tasks, New task, Task rules.
- Lists of tasks (number, title, priority, state, group, taken by, due), watchers and rules.
- The task form: Task, Who and when, Description, From a rule; watchers as a related list; the
  journal with work notes and comments. The rule form lists the tasks it opened.
- Widgets on the Tasks dashboard: My work and To take in my groups (lists), Overdue in my groups
  (figure), My groups' work by priority (donut).
- Form menu actions: Watch, Stop watching.

## Workflows, approvals and jobs

State flow on `tasks.task.state`, starting at `new`:

| Move | From → to | Who |
|---|---|---|
| take | new → in_progress | Members of the task's group (`group in @my_groups`); sets taken_by |
| wait, resume | in_progress ⇄ waiting | Whoever may change the task |
| finish, finish_waiting | in_progress, waiting → done | Whoever may change the task |
| give_back, give_back_waiting | in_progress, waiting → new | Whoever took it (`taken_by = @me`); clears taken_by |
| close | new → done | `tasks.manager` (and rules) |
| cancel | any open → cancelled | Whoever may change the task |

Moving a taken task to another group sets it back to new and clears taken_by. Done and cancelled
set `closed`.

Events it listens for:

| Event | What it does |
|---|---|
| `sys.job_failed` | Active rules opening on it open a task about the schedule, or count a repeat on the one still open (one per rule and record) |
| `sys.job_recovered` | Active rules closing on it mark their open task about the schedule done, with a work note |
| `cmdb.device_down`, `cmdb.device_recovered`, `cmdb.certificate_expiring`, `cmdb.certificate_expired`, `cmdb.review_waiting` | The same for rules on the CMDB's events, when the CMDB is installed |
| `sys.comment_added`, `sys.work_note_added` | A task's watchers are notified, not the author; work notes only to watchers in the task's group |

Job `tasks.due` (schedule "Due tasks, daily", 06:00 UTC): open tasks due tomorrow are told to
whoever took them, or to their group; overdue ones once (`told`).

Notification kinds: `tasks.new` (a new or moved task, to the group), `tasks.said` (to watchers),
`tasks.due` (due tomorrow, overdue).

## API

None beyond the generic records, comments and actions API.

## Outside systems

None.
