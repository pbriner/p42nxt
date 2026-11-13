"""Writing themes into records, checking the ones admins make, and the theme to show."""

from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.hooks import Change, TableHook
from p42cloud.data.presenting import translated
from p42cloud.data.settings import Setting
from p42cloud.data.types import Invalid
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.ui.declared import label_of, sync
from p42cloud.ui.themes.models import HEX, MAX_RADIUS, THEME_NAME, TOKENS, Theme

FALLBACK = "violet"


def _theme_name(given: Any) -> str:
    if not isinstance(given, str) or not THEME_NAME.match(given):
        raise Invalid("A theme's name, like violet.")
    return given


DEFAULT_THEME = Setting(
    "sys.theme",
    "Theme",
    FALLBACK,
    check=_theme_name,
    help="The theme everyone sees unless they choose another in their profile.",
    public=True,
)


def color_problems(colors: Any) -> list[str]:
    """What's wrong with a theme's colours: {token: [light, dark]} in #rrggbb."""
    if not isinstance(colors, dict):
        return ['Colours are {"primary": ["#7d00fa", "#d5baff"], …}.']
    found: list[str] = []
    for token, pair in cast(dict[Any, Any], colors).items():
        if token not in TOKENS:
            found.append(f"No colour {token!r}.")
        elif (
            not isinstance(pair, list | tuple)
            or len(cast(list[Any], pair)) != 2  # noqa: PLR2004 - light and dark
            or not all(isinstance(c, str) and HEX.match(c) for c in cast(list[Any], pair))
        ):
            found.append(f"{token}: a light and a dark colour, each like #7d00fa.")
    return found


def _check(theme: Theme) -> list[FieldError]:
    where = f"themes.{theme.name}"
    found = [FieldError(where, "bad_color", m) for m in color_problems(theme.colors)]
    if not THEME_NAME.match(theme.name):
        found.append(FieldError(where, "bad_name", "a-z, 0-9 and _, starting with a letter."))
    if not 0 <= theme.radius <= MAX_RADIUS:
        found.append(FieldError(where, "bad_radius", f"0 to {MAX_RADIUS} pixels."))
    return found


async def write_themes(conn: AsyncConnection, module: str, themes: tuple[Theme, ...]) -> None:
    found = [p for t in themes for p in _check(t)]
    if found:
        raise Problem(422, "invalid_themes", "The themes can't be used.", errors=found)
    rows = [
        {
            "name": t.name,
            "label": label_of(t.label),
            "description": label_of(t.description),
            "colors": {k: list(v) for k, v in t.colors.items()},
            "radius": t.radius,
            "cards": t.cards,
            "font": t.font,
        }
        for t in themes
    ]
    await sync(conn, "sys.theme", module, rows, frozenset({"label", "description", "colors"}))


def _invalid(name: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(name, "invalid", message)],
    )


async def _check_change(change: Change) -> None:
    """Admins make their own themes; the ones modules bring are copied, not changed."""
    if change.operation == "create":
        change.values["custom"] = True
        change.values["module"] = "u"
    else:
        custom = await change.records.conn.scalar(
            text("SELECT custom FROM sys.theme WHERE sys_id = :id"), {"id": change.sys_id}
        )
        if not custom:
            raise Problem(
                409, "declared_only", "This theme comes with a module; copy it to change it."
            )
        if {"custom", "module"} & set(change.values):
            raise _invalid("custom", "Set by the system.")
    if "name" in change.values and not THEME_NAME.match(str(change.values["name"])):
        raise _invalid("name", "a-z, 0-9 and _, starting with a letter.")
    if "colors" in change.values:
        problems = color_problems(change.values["colors"])
        if problems:
            raise _invalid("colors", " ".join(problems))
    radius = change.values.get("radius")
    if isinstance(radius, int) and not 0 <= radius <= MAX_RADIUS:
        raise _invalid("radius", f"0 to {MAX_RADIUS} pixels.")


TABLE_HOOKS = (TableHook("sys.theme", before=_check_change),)


async def theme(conn: AsyncConnection, wanted: str | None, language: str) -> dict[str, Any]:
    """The theme to show: the one asked for, else the installation's default, else Violet."""
    default = await conn.scalar(
        text("SELECT value #>> '{}' FROM sys.setting WHERE name = 'sys.theme' AND deleted IS NULL")
    )
    for name in (wanted, default, FALLBACK):
        if not name:
            continue
        row = (
            await conn.execute(
                text(
                    "SELECT name, label, description, colors, radius, cards, font FROM sys.theme"
                    " WHERE name = :n AND deleted IS NULL"
                ),
                {"n": name},
            )
        ).first()
        if row is not None:
            return {
                "name": row.name,
                "label": translated(row.label, language),
                "description": translated(row.description, language),
                "colors": {
                    token: {"light": pair[0], "dark": pair[1]} for token, pair in row.colors.items()
                },
                "radius": row.radius,
                "cards": row.cards,
                "font": row.font,
            }
    return {"name": None, "colors": {}, "radius": 4, "cards": "outlined", "font": "roboto"}
