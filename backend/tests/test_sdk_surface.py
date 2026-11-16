"""The SDK only grows inside a major version: what modules were built on keeps working.

`sdk_surface.json` is the SDK's public surface as released: every name, and the parameters of
everything callable (functions, classes, `Client`'s methods). A name taken away, a parameter
removed, renamed, moved or made required, or a new required parameter breaks modules built
before, so the test refuses it. Additions are fine but must be recorded, so they're protected
from then on:

    P42_SDK_SURFACE=write uv run pytest tests/test_sdk_surface.py
"""

import annotationlib
import dataclasses
import inspect
import json
import os
from pathlib import Path
from typing import Any

from p42cloud import sdk

SURFACE = Path(__file__).with_name("sdk_surface.json")
# Exported as types for hints only: modules use them through `Client`, so their methods aren't
# part of the promise.
TYPES_ONLY = frozenset({"Records"})
Param = dict[str, Any]


def _params(target: Any) -> list[Param] | None:
    try:
        # Annotations as written: only names and kinds count, and some refer to types
        # imported for type checking only.
        signature = inspect.signature(target, annotation_format=annotationlib.Format.STRING)
    except TypeError, ValueError:
        return None
    return [
        {
            "name": p.name,
            "kind": p.kind.name,
            "required": p.default is inspect.Parameter.empty
            and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD),
        }
        for p in signature.parameters.values()
        if p.name not in ("self", "cls")
    ]


def _methods(cls: type[object]) -> dict[str, list[Param] | None]:
    if dataclasses.is_dataclass(cls) or issubclass(cls, BaseException):
        return {}
    members: dict[str, Any] = dict(cls.__dict__)
    return {
        name: _params(member)
        for name, member in members.items()
        if not name.startswith("_") and (callable(member) or isinstance(member, classmethod))
    }


def surface() -> dict[str, Any]:
    found: dict[str, Any] = {}
    for name in sorted(sdk.__all__):
        item = getattr(sdk, name)
        entry: dict[str, Any] = {"params": _params(item) if callable(item) else None}
        if inspect.isclass(item) and name not in TYPES_ONLY:
            entry["methods"] = _methods(item)
        found[name] = entry
    return found


def _breaks(where: str, before: list[Param] | None, now: list[Param] | None) -> list[str]:
    if before is None:
        return []
    if now is None:
        return [f"{where}: no longer callable"]
    found: list[str] = []
    named = {p["name"]: p for p in now}
    for position, old in enumerate(before):
        new = named.get(old["name"])
        if new is None:
            found.append(f"{where}: parameter {old['name']!r} removed")
            continue
        if new["kind"] != old["kind"]:
            found.append(f"{where}: parameter {old['name']!r} is now {new['kind']}")
        if new["required"] and not old["required"]:
            found.append(f"{where}: parameter {old['name']!r} is now required")
        positional = ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD")
        if old["kind"] in positional and now.index(new) != position:
            found.append(f"{where}: parameter {old['name']!r} moved")
    old_names = {p["name"] for p in before}
    found += [
        f"{where}: new parameter {p['name']!r} is required"
        for p in now
        if p["name"] not in old_names and p["required"]
    ]
    return found


def breaking(before: dict[str, Any], now: dict[str, Any]) -> list[str]:
    """How `now` breaks modules built on `before`; empty if it doesn't."""
    found: list[str] = []
    for name, old in before.items():
        new = now.get(name)
        if new is None:
            found.append(f"{name}: removed from the SDK")
            continue
        found += _breaks(name, old["params"], new["params"])
        methods: dict[str, list[Param] | None] = old.get("methods") or {}
        for method, params in methods.items():
            if method not in (new.get("methods") or {}):
                found.append(f"{name}.{method}: removed")
            else:
                found += _breaks(f"{name}.{method}", params, new["methods"][method])
    return found


def test_the_sdk_only_grows() -> None:
    now = surface()
    if os.environ.get("P42_SDK_SURFACE") == "write":
        SURFACE.write_text(json.dumps(now, indent=1, sort_keys=True) + "\n")
    before = json.loads(SURFACE.read_text())
    assert breaking(before, now) == [], "This breaks modules built on the SDK as released."
    assert now == before, (
        "The SDK grew: record it with P42_SDK_SURFACE=write uv run pytest tests/test_sdk_surface.py"
    )


def test_breaking_changes_are_found() -> None:
    req: Param = {"name": "a", "kind": "POSITIONAL_OR_KEYWORD", "required": True}
    opt: Param = {"name": "b", "kind": "KEYWORD_ONLY", "required": False}
    before: dict[str, Any] = {
        "f": {"params": [req, opt]},
        "C": {"params": [], "methods": {"m": [req]}},
    }
    assert breaking(before, before) == []
    grown: dict[str, Any] = {
        "f": {"params": [req, opt, opt | {"name": "c"}]},
        "C": before["C"],
        "g": {},
    }
    assert breaking(before, grown) == []
    assert breaking(before, {"C": before["C"]}) == ["f: removed from the SDK"]
    assert breaking(before, {"f": {"params": [req, opt | {"required": True}]}, "C": before["C"]})
    assert breaking(before, {"f": {"params": [req, opt, req | {"name": "x"}]}, "C": before["C"]})
    assert breaking(before, {"f": before["f"], "C": {"params": [], "methods": {}}}) == [
        "C.m: removed"
    ]
