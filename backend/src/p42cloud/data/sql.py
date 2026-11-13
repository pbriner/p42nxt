"""Names and literals put into SQL the engine writes itself (table definitions).

Names come from the metadata, and are checked again here, so nothing from outside ever reaches a
statement unquoted. Values never go into SQL this way: they are bound parameters.
"""

import re

NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
SCHEMA = re.compile(r"^[a-z][a-z0-9]{0,15}$")


def ident(name: str) -> str:
    """A table, column or constraint name, always quoted (so `table` works as a name)."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name):
        raise ValueError(f"not a plain name: {name!r}")
    return f'"{name}"'


def qualified(schema: str, name: str) -> str:
    return f"{ident(schema)}.{ident(name)}"


def literal(value: str) -> str:
    """A string constant in a definition (choice values, patterns): the engine's own, checked."""
    if "\\" in value or "\0" in value:
        raise ValueError(f"not allowed in a literal: {value!r}")
    return "'" + value.replace("'", "''") + "'"
