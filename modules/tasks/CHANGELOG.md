# Changelog

## 0.2.0

- Task rules also open on the CMDB's events, when the CMDB is installed: a device stops
  answering (closed when it answers again), a certificate ends soon or has ended, something
  found waits for review.

## 0.1.0

- Tasks for groups: title, description, impact and urgency giving the priority, a due date, and
  what the task is about (any record). Numbered TASK-0000001.
- Members of a task's group take it (**Take**), and whoever took it can **Give back**; **Wait**,
  **Resume**, **Mark done** and **Cancel**. Everyone with the Tasks role sees every task and
  makes tasks for any group; only the group's members work on them.
- My work, My groups' work and All tasks; a dashboard with what's mine, what's left to take,
  what's overdue and the work by priority.
- Work notes for the group and comments for everyone, in the task's journal. Watchers (who made
  it, who took it, and anyone who chooses to) hear of what's said.
- Task rules: a failed job opens a task for a group, a repeat is counted on it, and the job
  working again closes it with a work note.
- A new task is told to its group; due tomorrow and overdue tasks are told each morning.
