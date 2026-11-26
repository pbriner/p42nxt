"""What agents add to the core: their tables, events and the job marking quiet ones."""

from p42cloud.data.hooks import TableHook
from p42cloud.services.agents import models, service
from p42cloud.services.events.models import EventType
from p42cloud.services.jobs.models import Job, JobRun, Schedule

TABLES = models.TABLES
HOOKS = (TableHook("sys.agent", after=service.follow_active),)
EVENTS = (
    EventType("agent_enrolled", "An agent is enrolled", about="sys.agent"),
    EventType("agent_quiet", "An agent goes quiet", about="sys.agent"),
    EventType("agent_back", "An agent is back", about="sys.agent"),
    EventType("agent_task_done", "An agent did a task", about="sys.agent_task"),
)


async def _quiet(run: JobRun) -> str:
    return f"{await service.mark_quiet(run.records.conn)} agent(s) went quiet."


JOBS = (Job("agents_quiet", "Find quiet agents", _quiet, timeout=60),)
SCHEDULES = (
    Schedule("agents_quiet", "Quiet agents, every 5 minutes", "agents_quiet", "*/5 * * * *"),
)
