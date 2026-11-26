"""A module whose widgets have its own dashboard links to that dashboard from its menu: otherwise
the dashboard exists but nobody finds it."""

from p42cloud.modules.registry import discover
from p42cloud.ui.navigation.models import Entry, Item, Section


def _pages(entries: tuple[Entry, ...]) -> set[str]:
    found: set[str] = set()
    for entry in entries:
        items: tuple[Item, ...] = entry.items if isinstance(entry, Section) else (entry,)
        found |= {i.path for i in items if i.kind == "page" and i.path}
    return found


def test_every_module_dashboard_is_in_its_menu() -> None:
    missing = [
        module.id
        for module in discover()
        if any(w.dashboard == module.id for w in module.widgets)
        and f"/dashboard/{module.id}" not in _pages(module.navigation)
    ]
    assert missing == []
