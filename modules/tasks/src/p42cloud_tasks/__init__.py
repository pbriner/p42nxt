"""Tasks: work for a group, which its members take themselves.

A task is about anything (any record), has an impact and an urgency that give its priority, and
goes from new (waiting in its group) to in progress (someone took it), waiting, done or
cancelled. Everyone with the Tasks role sees every task; only the group's members work on it.
Rules turn events (a failed job) into tasks and close them when the matching event comes (the job
works again). Watchers hear of comments, work notes and due dates.
"""

from pathlib import Path

from p42cloud.sdk import (
    AnyRecord,
    Boolean,
    Choice,
    Date,
    DateTime,
    Flow,
    FormSection,
    FormView,
    Grant,
    Integer,
    Item,
    Job,
    ListView,
    Module,
    NotificationKind,
    Number,
    Reference,
    RelatedList,
    Role,
    Schedule,
    Section,
    String,
    Subscription,
    Table,
    TableHook,
    Text,
    Transition,
    UIAction,
    Widget,
    package_version,
)

from p42cloud_tasks.logic import (
    EVENT_CHOICES,
    EVENTS,
    LEVELS,
    OPEN,
    PRIORITIES,
    STATES,
    TAKEN,
    check_rule,
    check_task,
    on_event,
    on_said,
    stop_watching,
    take_watch,
    tell_due,
    watch_and_tell,
)

ALL = ("read", "create", "write", "delete", "restore")
MINE = {"field": "person", "op": "eq", "value": "@me"}
IN_MY_GROUPS = {"field": "group", "op": "in", "value": "@my_groups"}
STILL_OPEN = {"field": "state", "op": "in", "value": list(OPEN)}
MY_WORK = {
    "and": [
        {"field": "taken_by", "op": "eq", "value": "@me"},
        {"field": "state", "op": "in", "value": list(TAKEN)},
    ]
}
MY_GROUPS = {"and": [IN_MY_GROUPS, STILL_OPEN]}
TO_TAKE = {"and": [IN_MY_GROUPS, {"field": "state", "op": "eq", "value": "new"}]}
OVERDUE = {"and": [IN_MY_GROUPS, STILL_OPEN, {"field": "due", "op": "lt", "value": "@today"}]}

TASK = Table(
    "task",
    "Task",
    {
        "number": Number("Number", prefix="TASK", digits=7),
        "title": String("Title", max_length=200, required=True),
        "description": Text("Description"),
        "state": Choice(
            "State",
            STATES,
            required=True,
            default="new",
            tones={"new": "tertiary", "in_progress": "primary", "cancelled": "muted"},
        ),
        "impact": Choice(
            "Impact",
            LEVELS,
            required=True,
            default="medium",
            help="How many people, systems or processes it affects.",
        ),
        "urgency": Choice(
            "Urgency", LEVELS, required=True, default="medium", help="How soon it must be solved."
        ),
        "priority": Choice(
            "Priority",
            PRIORITIES,
            read_only=True,
            tones={"critical": "error", "high": "tertiary", "low": "muted"},
            help="From impact and urgency.",
        ),
        "group": Reference("Group", "sys.group", required=True, on_delete="restrict"),
        "taken_by": Reference("Taken by", "sys.person", read_only=True),
        "due": Date("Due"),
        "about_table": String("About table", max_length=80),
        "about": AnyRecord("About", table_field="about_table"),
        "closed": DateTime("Closed", read_only=True),
        "rule": Reference("Opened by rule", "tasks.rule", read_only=True),
        "repeats": Integer("Times seen", read_only=True),
        "last_seen": DateTime("Last seen", read_only=True),
        # When it was told as overdue, so that's said once.
        "told": Date("Told overdue", read_only=True),
    },
    label_plural="Tasks",
    display="title",
    comments=True,
    work_notes=True,
)

WATCHER = Table(
    "watcher",
    "Watcher",
    {
        "task": Reference("Task", "tasks.task", required=True, on_delete="cascade"),
        "person": Reference("Person", "sys.person", required=True, default="@me"),
    },
    label_plural="Watchers",
    unique_together=(("task", "person"),),
    display="person",
)

RULE = Table(
    "rule",
    "Task rule",
    {
        "name": String("Name", max_length=120, required=True),
        "active": Boolean("Active", required=True, default=True),
        "event": Choice("Opens on", EVENT_CHOICES, required=True),
        "resolved_by": Choice("Closes on", EVENT_CHOICES),
        "title": String(
            "Task title",
            max_length=200,
            required=True,
            help="The new task's title. {message} and the event's other details are filled in.",
        ),
        "group": Reference("Group", "sys.group", required=True, on_delete="restrict"),
        "impact": Choice("Impact", LEVELS, required=True, default="medium"),
        "urgency": Choice("Urgency", LEVELS, required=True, default="medium"),
        "due_days": Integer("Due in days", help="Empty: no due date."),
    },
    label_plural="Task rules",
    display="name",
)

TABLES = (TASK, WATCHER, RULE)

USER = Role(
    "tasks.user",
    "Tasks user",
    grants=(
        Grant("tasks.task", ("read", "create")),
        # Only the group's members work on its tasks.
        Grant("tasks.task", ("write",), condition=IN_MY_GROUPS),
        # Whoever made a task may change or cancel it while nobody has taken it.
        Grant(
            "tasks.task",
            ("write",),
            condition={
                "and": [
                    {"field": "created_by", "op": "eq", "value": "@me"},
                    {"field": "state", "op": "eq", "value": "new"},
                ]
            },
        ),
        Grant("tasks.watcher", ("read",)),
        Grant("tasks.watcher", ("create", "delete"), condition=MINE),
        Grant("tasks.rule", ("read",)),
        # Which groups take work: as everyone reads them (Tasks' own jobs aren't "everyone").
        Grant("sys.group", ("read",), fields=("name", "description", "assignable")),
    ),
    description="Sees every task, makes tasks for any group, works on its groups' tasks.",
)
MANAGER = Role(
    "tasks.manager",
    "Tasks manager",
    grants=(
        *(Grant(f"tasks.{t.name}", ALL) for t in TABLES),
        # Who is in which group: work notes are told only to watchers in the task's group.
        Grant("sys.membership", ("read",), fields=("group", "person")),
    ),
    includes=("tasks.user",),
    description="Does everything with tasks, and sets up the rules that open them.",
)

MARK_DONE = "Mark done"
TO_GIVE_BACK = {"field": "taken_by", "op": "eq", "value": "@me"}
FLOW = Flow(
    "tasks.task",
    "state",
    initial="new",
    transitions=(
        Transition("take", "Take", "new", "in_progress", icon="back_hand", condition=IN_MY_GROUPS),
        Transition("wait", "Wait", "in_progress", "waiting", icon="pause"),
        Transition("resume", "Resume", "waiting", "in_progress", icon="play_arrow"),
        Transition("finish", MARK_DONE, "in_progress", "done", icon="task_alt"),
        Transition("finish_waiting", MARK_DONE, "waiting", "done", icon="task_alt"),
        Transition(
            "give_back", "Give back", "in_progress", "new", icon="undo", condition=TO_GIVE_BACK
        ),
        Transition(
            "give_back_waiting", "Give back", "waiting", "new", icon="undo", condition=TO_GIVE_BACK
        ),
        # A task nobody took, closed as done: by a manager, or by its rule.
        Transition("close", MARK_DONE, "new", "done", icon="task_alt", roles=("tasks.manager",)),
        Transition(
            "cancel",
            "Cancel",
            None,
            "cancelled",
            icon="cancel",
            condition=STILL_OPEN,
            confirm="Cancel the task?",
        ),
    ),
)

WIDGETS = (
    Widget(
        "my_work",
        "My work",
        "list",
        dashboard="tasks",
        list="tasks.task",
        filter=MY_WORK,
        icon="assignment_ind",
        sizes=("wide", "large"),
        size="wide",
    ),
    Widget(
        "to_take",
        "To take in my groups",
        "list",
        dashboard="tasks",
        list="tasks.task",
        filter=TO_TAKE,
        icon="inbox",
        sizes=("wide", "large"),
        size="wide",
    ),
    Widget(
        "overdue",
        "Overdue in my groups",
        "figure",
        dashboard="tasks",
        list="tasks.task",
        filter=OVERDUE,
        icon="alarm",
        accent=6,
    ),
    Widget(
        "by_priority",
        "My groups' work by priority",
        "chart",
        dashboard="tasks",
        list="tasks.task",
        filter=MY_GROUPS,
        group="priority",
        chart="donut",
        icon="priority_high",
    ),
)

module = Module(
    id="tasks",
    name="Tasks",
    publisher="p42",
    version=package_version("p42cloud-tasks"),
    depends_on={"sys": ">=0.47,<1"},
    tables=TABLES,
    roles=(USER, MANAGER),
    hooks=(
        TableHook("tasks.task", before=check_task, after=watch_and_tell),
        TableHook("tasks.rule", before=check_rule),
    ),
    flows=(FLOW,),
    subscriptions=(
        *(
            Subscription(event, on_event, label=f"Task rules: {label.lower()}")
            for event, label in EVENTS.values()
        ),
        Subscription("sys.comment_added", on_said, label="Watchers hear of comments"),
        Subscription("sys.work_note_added", on_said, label="Watchers hear of work notes"),
    ),
    notification_kinds=(
        NotificationKind("new", "A new task for your group", "To the group's members."),
        NotificationKind(
            "said",
            "News on a task you watch",
            "To its watchers: a comment, or a work note (to watchers in its group).",
        ),
        NotificationKind(
            "due",
            "A task is due tomorrow or overdue",
            "To whoever took it, or its group while nobody has.",
        ),
    ),
    jobs=(Job("due", "Tell due and overdue tasks", tell_due, timeout=120),),
    schedules=(Schedule("due", "Due tasks, daily", "due", "0 6 * * *"),),
    actions=(
        UIAction(
            "watch",
            "tasks.task",
            "Watch",
            places=("form_menu",),
            icon="visibility",
            operation="read",
            handler=take_watch,
        ),
        UIAction(
            "stop_watching",
            "tasks.task",
            "Stop watching",
            places=("form_menu",),
            icon="visibility_off",
            operation="read",
            handler=stop_watching,
        ),
    ),
    widgets=WIDGETS,
    docs=Path(__file__).parent / "docs",
    translations=Path(__file__).parent / "translations",
    description="Work for groups, taken by their members, opened and closed by rules on events.",
    category="Work",
    icon="task_alt",
    views=(
        ListView(
            "tasks.task",
            ("number", "title", "priority", "state", "group", "taken_by", "due"),
            sort=("due", "number"),
        ),
        ListView("tasks.watcher", ("task", "person"), sort=("task",), searchable=False),
        ListView("tasks.rule", ("name", "event", "resolved_by", "group", "active"), sort=("name",)),
    ),
    forms=(
        FormView(
            "tasks.task",
            (
                FormSection("Task", ("number", "title", "state", "priority", "impact", "urgency")),
                FormSection("Who and when", ("group", "taken_by", "due", "about", "closed")),
                FormSection("Description", ("description",), columns=1),
                FormSection("From a rule", ("rule", "repeats", "last_seen")),
            ),
            related=(RelatedList("tasks.watcher", "task"),),
        ),
        FormView(
            "tasks.rule",
            (
                FormSection("Rule", ("name", "active", "event", "resolved_by")),
                FormSection("The task", ("title", "group", "impact", "urgency", "due_days")),
            ),
            related=(RelatedList("tasks.task", "rule"),),
        ),
    ),
    navigation=(
        Section(
            "Tasks",
            (
                Item(
                    "dashboard",
                    "Dashboard",
                    kind="page",
                    path="/dashboard/tasks",
                    icon="monitoring",
                ),
                Item("my_work", "My work", "tasks.task", filter=MY_WORK, icon="assignment_ind"),
                Item("my_groups", "My groups' work", "tasks.task", filter=MY_GROUPS, icon="groups"),
                Item("all", "All tasks", "tasks.task", icon="task_alt"),
                Item("new", "New task", "tasks.task", kind="create", icon="add"),
                Item("rules", "Task rules", "tasks.rule", icon="rule"),
            ),
        ),
    ),
)
