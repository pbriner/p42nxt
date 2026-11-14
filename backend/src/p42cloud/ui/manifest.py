"""What the screens add to the core: their tables, hooks, settings, views, navigation, themes."""

from p42cloud.ui.favorites import models as favorites
from p42cloud.ui.forms.models import FORM_VIEW
from p42cloud.ui.navigation.models import NAV_ITEM, Item, Section
from p42cloud.ui.preferences import models as preferences
from p42cloud.ui.shell import STAGE
from p42cloud.ui.themes import builtin
from p42cloud.ui.themes import service as themes
from p42cloud.ui.themes.models import THEME
from p42cloud.ui.views import models as views
from p42cloud.ui.views import service as view_service
from p42cloud.ui.views.models import ListView

TABLES = (
    NAV_ITEM,
    *views.TABLES,
    FORM_VIEW,
    THEME,
    preferences.PREFERENCE,
    favorites.FAVORITE,
)
HOOKS = (
    *view_service.TABLE_HOOKS,
    *themes.TABLE_HOOKS,
    *preferences.TABLE_HOOKS,
    *favorites.TABLE_HOOKS,
)
SETTINGS = (STAGE, themes.DEFAULT_THEME)
THEMES = builtin.THEMES

# The core's lists worth finding in the search; its other tables get made, unsearched views.
VIEWS = (
    ListView("sys.person", ("name", "email", "kind", "active"), search=("name", "email")),
    ListView("sys.group", ("name", "description", "assignable")),
    ListView("sys.role", ("label", "name", "module", "powerful"), search=("label", "name")),
    ListView("sys.module", ("label", "name", "version", "enabled"), sort=("label",)),
)

NAVIGATION = (
    Section(
        "People and access",
        (
            Item("people", "People", "sys.person", icon="person", keywords=("users",)),
            Item("groups", "Groups", "sys.group", icon="group"),
            Item("roles", "Roles", "sys.role", icon="badge", keywords=("permissions",)),
            Item("providers", "Sign-in providers", "sys.identity_provider", icon="login"),
            Item("tokens", "API tokens", "sys.api_token", icon="key"),
        ),
    ),
    Section(
        "Modules and data",
        (
            Item("modules", "Modules", "sys.module", icon="extension"),
            Item("updates", "Available updates", "sys.module_update", icon="update"),
            Item("tables", "Tables", "sys.table", icon="table"),
            Item("settings", "Settings", "sys.setting", icon="tune"),
        ),
    ),
    Section(
        "Appearance",
        (Item("themes", "Themes", "sys.theme", icon="palette", keywords=("colours", "colors")),),
    ),
)
