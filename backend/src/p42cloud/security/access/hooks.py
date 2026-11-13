"""What access does when its records change through the records service."""

from p42cloud.data.hooks import TableHook
from p42cloud.security.access import groups, guards, sharing

TABLE_HOOKS = (
    TableHook("sys.group_role", before=guards.group_role),
    TableHook("sys.membership", before=guards.membership),
    TableHook("sys.group", before=guards.group, after=groups.group_changed),
    TableHook("sys.group", before=groups.check_group),
    TableHook("sys.role", before=guards.role),
    TableHook("sys.grant", before=guards.grant, after=guards.recompute),
    TableHook("sys.role_include", before=guards.role_include, after=guards.recompute),
    TableHook("sys.person", after=groups.person_changed),
    TableHook("sys.share", before=sharing.share),
)
