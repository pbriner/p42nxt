# Tasks

Work for a group, which its members take themselves. Tasks also open by themselves when
something happens, like a scheduled job failing.

## A task

A task has a **title**, a **description** and the **group** whose work it is. It can be **about**
any record (a schedule, an order, a person), opened from the task. Its **due** date is optional.

**Impact** (how many people, systems or processes it affects) and **urgency** (how soon it must
be solved) give its **priority**:

| Impact \ urgency | High | Medium | Low |
|---|---|---|---|
| **High** | Critical | High | Moderate |
| **Medium** | High | Moderate | Low |
| **Low** | Moderate | Low | Low |

## Taking work

A task is given to a group, never to a person. Members of the group take it themselves:

| State | Meaning | Buttons |
|---|---|---|
| New | Waiting in its group | **Take** (the group's members) |
| In progress | Someone took it | **Wait**, **Mark done**, **Give back** (whoever took it) |
| Waiting | Waiting for something or someone | **Resume**, **Mark done**, **Give back** |
| Done, Cancelled | Closed | |

**Cancel** works in every open state. Whoever made a task may change or cancel it while nobody
has taken it. Moving a task to another group puts it there as new, to be taken again.

## Who sees what

Everyone with the Tasks role sees every task and may make tasks for any group. Only members of a
task's group change, take and close it. Tasks managers do everything, including the rules.

The navigator has **My work** (what you took), **My groups' work** (everything open in your
groups) and **All tasks**. The Tasks dashboard shows your work, what's left to take, what's
overdue and your groups' work by priority.

## Journal

A task's journal holds its **work notes** (for its group: everyone who may change it), its
**comments** (for everyone who sees it) and its changes, newest first.

## Watchers

Whoever made a task, and whoever takes it, watch it. Anyone may **Watch** a task (or **Stop
watching**) from its form's menu. Watchers hear of new comments; of work notes only watchers in
the task's group hear.

## Notices

- A new task, or one moved to another group, is told to the group's members.
- Each morning, open tasks due tomorrow are told to whoever took them, or to their group while
  nobody has. An overdue task is told once.

## Rules

A **task rule** opens a task when an event happens, and can close it when another one comes:

| Field | Meaning |
|---|---|
| Opens on | The event, like "A scheduled job fails" |
| Closes on | Optional, like "A scheduled job works again" |
| Task title | The new task's title; `{message}`, `{job}` and the event's other details are filled in |
| Group, impact, urgency, due in days | For the new task |

There is one open task per rule and record: if the job fails again, the open task counts it
(**Times seen**, **Last seen**) instead of opening another. When the closing event comes, the
task is marked done with a work note saying why. A failed job's own notice to administrators
stays as it is.
