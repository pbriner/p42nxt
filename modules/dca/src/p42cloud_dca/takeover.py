"""Taking over plans and orders from a previous installation, from an export file.

A person adds a takeover (the export, as JSON); a job imports it for them as the module's service
account: their plans (inactive and dry runs only, so nothing buys next to the old installation),
the plans' tiers, and every order with its original time and Kraken order. Running it again adds
only what's new: each record remembers where it came from (`source_ref`).
"""

from decimal import Decimal, InvalidOperation
from typing import Any, cast

from p42cloud.sdk import Client, JobRun, Problem

from p42cloud_dca.paging import every

SOURCE = "takeover"  # source_ref: takeover:<the record's ID there>
PAIRS = {"XBTCHF", "XBTEUR", "XBTUSD", "ETHCHF", "ETHEUR", "ETHUSD"}
NUMBERS = (
    "amount",
    "max_price",
    "monthly_limit",
    "cooldown_hours",
    "target_profit_percent",
    "target_profit",
    "loss_limit_percent",
    "loss_limit",
)
AS_THEY_ARE = ("name", "mode", "reference", "dynamic", "end_date")
WHOLE = ("deposit_day", "buy_hour")
ORDER_NUMBERS = ("amount", "price", "cost", "fee")
ORDER_AS_THEY_ARE = ("state", "currency", "userref", "kraken_txid", "placed_at", "error")
STATES = {"dry_run", "submitting", "filled", "unknown", "failed"}
STRATEGIES = {"scheduled", "dynamic", "manual"}


def _list(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    found: Any = data.get(key) or []
    if not isinstance(found, list):
        raise Problem(422, "validation", f"The export's {key} must be a list.")
    return [cast(dict[str, Any], r) for r in cast(list[Any], found) if isinstance(r, dict)]


def _number(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        return str(Decimal(str(value)))
    except InvalidOperation as error:
        raise Problem(422, "validation", f"Not a number: {value!r}.") from error


def _plan_values(old: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {k: old[k] for k in AS_THEY_ARE if old.get(k) is not None}
    values |= {k: _number(old[k]) for k in NUMBERS if old.get(k) not in (None, "")}
    values |= {k: int(Decimal(str(old[k]))) for k in WHOLE if old.get(k) not in (None, "")}
    return values


def _order_values(old: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {k: old[k] for k in ORDER_AS_THEY_ARE if old.get(k) not in (None, "")}
    values |= {k: _number(old[k]) for k in ORDER_NUMBERS if old.get(k) not in (None, "")}
    values.setdefault("placed_at", old.get("created"))
    strategy = old.get("strategy")
    values["strategy"] = strategy if strategy in STRATEGIES else "manual"
    values["pair"] = str(old.get("pair") or "XBTCHF").upper()
    return values


async def _known(client: Client, table: str) -> dict[str, dict[str, Any]]:
    found = await every(
        client, table, where={"field": "source_ref", "op": "starts_with", "value": f"{SOURCE}:"}
    )
    return {str(r["source_ref"]): r for r in found}


async def _plans(
    client: Client, owner: str, plans: list[dict[str, Any]], tiers: list[dict[str, Any]]
) -> tuple[dict[str, str], int, int, int]:
    """The plans (new ones with their tiers): old ID → new ID, and how many were made/skipped."""
    known = await _known(client, "dca.plan")
    by_old: dict[str, str] = {}
    made_plans = made_tiers = skipped = 0
    for old in plans:
        ref = f"{SOURCE}:{old['sys_id']}"
        pair = str(old.get("pair") or "XBTCHF").upper()
        if ref in known:
            by_old[str(old["sys_id"])] = known[ref]["sys_id"]
            continue
        if pair not in PAIRS:
            skipped += 1
            continue
        made = await client.create(
            "dca.plan",
            _plan_values(old)
            | {
                "owner": owner,
                "pair": pair.lower(),
                "active": False,  # never buys next to the old installation
                "live": False,
                "source_ref": ref,
            },
        )
        by_old[str(old["sys_id"])] = made["sys_id"]
        made_plans += 1
        for tier in (t for t in tiers if str(t.get("plan")) == str(old["sys_id"])):
            await client.create(
                "dca.tier",
                {
                    "owner": owner,
                    "plan": made["sys_id"],
                    "threshold_percent": _number(tier.get("threshold_percent")),
                    "amount": _number(tier.get("amount")),
                    "enabled": bool(tier.get("enabled", True)),
                },
            )
            made_tiers += 1
    return by_old, made_plans, made_tiers, skipped


async def take_over(client: Client, owner: str, data: dict[str, Any]) -> str:
    """Imports one export for its owner; what it did."""
    by_old, plans, tiers, skipped = await _plans(
        client, owner, _list(data, "plans"), _list(data, "tiers")
    )
    known = await _known(client, "dca.order")
    orders = 0
    ordered = sorted(
        _list(data, "orders"), key=lambda o: str(o.get("placed_at") or o.get("created") or "")
    )
    for old in ordered:
        ref = f"{SOURCE}:{old['sys_id']}"
        if ref in known:
            continue
        plan = by_old.get(str(old.get("plan")))
        if plan is None or old.get("state") not in STATES:
            skipped += 1
            continue
        await client.create(
            "dca.order", _order_values(old) | {"owner": owner, "plan": plan, "source_ref": ref}
        )
        orders += 1
    said = f"{plans} plan(s) with {tiers} tier(s) and {orders} order(s) taken over"
    return said + (f"; {skipped} skipped." if skipped else ".")


async def run(job: JobRun) -> str:
    """Imports the takeovers people added."""
    client = Client(job.records)
    waiting = await client.list(
        "dca.takeover", where={"field": "state", "op": "eq", "value": "waiting"}, limit=20
    )
    for takeover in waiting["records"]:
        data = takeover["data"]
        try:
            if not isinstance(data, dict):
                raise Problem(422, "validation", "The export is a JSON object.")
            said = await take_over(client, takeover["owner"], cast(dict[str, Any], data))
            state = "done"
        except Problem as problem:
            fields = "; ".join(f"{e.field}: {e.message}" for e in problem.errors)
            said = " ".join(x for x in (problem.title, problem.detail, fields) if x)
            state = "failed"
        await client.update(
            "dca.takeover",
            takeover["sys_id"],
            # The export isn't kept: its records are what counts now.
            {"state": state, "message": said[:2000], "data": None},
            takeover["_etag"],
        )
    return f"{len(waiting['records'])} takeover(s)."
