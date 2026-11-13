"""Registers what access does when its records change through the records service."""

from p42cloud.data.hooks import HOOKS
from p42cloud.security.access import groups, guards, sharing


def register() -> None:
    HOOKS.on("sys.group_role", before=guards.group_role)
    HOOKS.on("sys.membership", before=guards.membership)
    HOOKS.on("sys.group", before=guards.group, after=groups.group_changed)
    HOOKS.on("sys.group", before=groups.check_group)
    HOOKS.on("sys.role", before=guards.role)
    HOOKS.on("sys.grant", before=guards.grant, after=guards.recompute)
    HOOKS.on("sys.role_include", before=guards.role_include, after=guards.recompute)
    HOOKS.on("sys.person", after=groups.person_changed)
    HOOKS.on("sys.share", before=sharing.share)
