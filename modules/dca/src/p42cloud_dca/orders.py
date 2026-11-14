"""Real orders: sent to Kraken as outside calls, so each is made at most once.

The order is recorded first (state "submitting") with a random reference, and the call with it in
the same transaction. The worker sends it; if the answer is lost, Kraken is asked by the
reference before anything is sent again. Kraken's final no puts the plan on hold; an order
still unclear after the last try too, until its owner has looked and pressed Resume.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from p42cloud.sdk import CallRefused, CallRun, Client, GaveUp, OutsideCall, Records

from p42cloud_dca import kraken
from p42cloud_dca.kraken import KrakenError, Pair


async def _send(http: httpx.AsyncClient, run: CallRun) -> dict[str, Any]:
    payload = run.payload
    try:
        txid = await kraken.add_order(
            http, str(payload["pair"]), Decimal(str(payload["volume"])), int(payload["userref"])
        )
    except KrakenError as error:
        if error.temporary:
            raise  # maybe placed, maybe not: asked about by its reference before sending again
        raise CallRefused(str(error)) from error
    try:
        fill = await kraken.order(http, txid)  # a market order is usually done at once
    except KrakenError, httpx.HTTPError:
        fill = None
    return (fill or kraken.Fill(txid, "pending", *(Decimal(0),) * 4)).answer()


async def _reconcile(http: httpx.AsyncClient, run: CallRun) -> dict[str, Any] | None:
    found = await kraken.find(http, int(run.payload["userref"]))
    return found.answer() if found else None


async def apply_fill(client: Client, order: dict[str, Any], fill: dict[str, Any]) -> str:
    """Records what Kraken says about an order; its new state."""
    values: dict[str, Any] = {
        "kraken_txid": fill["txid"],
        "reconciled_at": datetime.now(UTC).isoformat(),
    }
    status = fill["status"]
    if status == "closed":
        values |= {
            "state": "filled",
            "amount": fill["volume"],
            "cost": fill["cost"],
            "fee": fill["fee"],
            "price": fill["price"],
        }
    elif status in ("canceled", "expired"):
        values |= {"state": "failed", "error": f"Kraken: {status}"}
    await client.update("dca.order", order["sys_id"], values, order["_etag"])
    return str(values.get("state", order["state"]))


async def tell_bought(client: Client, order: dict[str, Any], plan: dict[str, Any]) -> None:
    pair = Pair(str(order["pair"]))
    await client.notify(
        "dca.buy",
        f"Bought: {plan['name']}",
        people=[plan["owner"]],
        message=f"Bought {order['amount']} {pair.asset} at {Decimal(str(order['price'])):.2f} "
        f"{pair.currency} ({Decimal(str(order['cost'])):.2f} {pair.currency}, fee "
        f"{Decimal(str(order['fee'])):.2f}).",
        link=f"/form/dca.plan/{plan['sys_id']}",
        severity="success",
    )


async def _done(records: Records, run: CallRun, answer: dict[str, Any]) -> None:
    client = Client(records)
    order = await client.get("dca.order", str(run.record_id))
    if await apply_fill(client, order, answer) == "filled":
        plan = await client.get("dca.plan", order["plan"])
        await tell_bought(client, await client.get("dca.order", order["sys_id"]), plan)


async def hold(client: Client, plan_id: str, reason: str) -> None:
    """Stops a plan until its owner looks and presses Resume."""
    plan = await client.get("dca.plan", plan_id)
    await client.update(
        "dca.plan",
        plan_id,
        {
            "status": "hold",
            "hold_reason": reason,
            "next_buy": None,
            "last_message": f"On hold: {reason}"[:500],
        },
        plan["_etag"],
    )
    await client.notify(
        "dca.hold",
        f"DCA plan on hold: {plan['name']}",
        people=[plan["owner"]],
        message=reason,
        link=f"/form/dca.plan/{plan_id}",
        severity="error",
    )


async def _gave_up(records: Records, run: CallRun, ending: GaveUp) -> None:
    client = Client(records)
    order = await client.get("dca.order", str(run.record_id))
    ref = run.payload["userref"]
    if ending.why == "unclear":
        await client.update(
            "dca.order",
            order["sys_id"],
            {"state": "unknown", "error": ending.message[:500]},
            order["_etag"],
        )
        await hold(
            client, order["plan"], f"Order {ref} is unclear: check it on Kraken, then Resume."
        )
        return
    await client.update(
        "dca.order",
        order["sys_id"],
        {"state": "failed", "error": ending.message[:500]},
        order["_etag"],
    )
    if ending.why == "refused":
        await hold(client, order["plan"], f"Kraken refused the order: {ending.message}")


PLACE_ORDER = OutsideCall(
    "place_order",
    "Place a DCA order on Kraken",
    connection_type="kraken",
    send=_send,
    reconcile=_reconcile,
    done=_done,
    gave_up=_gave_up,
    retries=3,
)
