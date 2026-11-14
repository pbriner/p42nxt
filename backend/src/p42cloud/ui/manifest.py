"""What the screens add to the core: their tables, hooks, settings, views, navigation, themes."""

from p42cloud.ui.actions.models import UI_ACTION
from p42cloud.ui.favorites import models as favorites
from p42cloud.ui.forms.models import FORM_VIEW
from p42cloud.ui.navigation.models import NAV_ITEM, Item, Section
from p42cloud.ui.preferences import models as preferences
from p42cloud.ui.rules import service as rules
from p42cloud.ui.rules.models import FORM_RULE
from p42cloud.ui.shell import APP_NAME, LANGUAGE, STAGE
from p42cloud.ui.themes import builtin
from p42cloud.ui.themes import service as themes
from p42cloud.ui.themes.models import THEME
from p42cloud.ui.views import models as views
from p42cloud.ui.views import service as view_service
from p42cloud.ui.views.models import ListView
from p42cloud.ui.widgets import models as widgets
from p42cloud.ui.widgets import service as widget_service
from p42cloud.ui.widgets.models import Widget

TABLES = (
    NAV_ITEM,
    *views.TABLES,
    FORM_VIEW,
    UI_ACTION,
    FORM_RULE,
    *widgets.TABLES,
    THEME,
    preferences.PREFERENCE,
    favorites.FAVORITE,
)
HOOKS = (
    *view_service.TABLE_HOOKS,
    *themes.TABLE_HOOKS,
    *preferences.TABLE_HOOKS,
    *favorites.TABLE_HOOKS,
    *rules.TABLE_HOOKS,
    *widget_service.TABLE_HOOKS,
)
SETTINGS = (STAGE, APP_NAME, LANGUAGE, themes.DEFAULT_THEME)
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

# On everyone's home, for those who may read people.
WIDGETS = (
    Widget(
        "people",
        "People",
        "figure",
        list="sys.person",
        filter={"field": "active", "op": "eq", "value": True},
        icon="group",
        accent=1,
        trend="created",
        shown="available",
    ),
)
