"""Partners: the companies and people one works with, shared by every module.

A partner is a supplier, a customer, a tenant, or several of these; it has contact persons and
contracts (a lease, a service or supply contract, an insurance), each with its period, amount
and the signed document attached. Finance books against partners and reads leases (the rent and
the advances for running costs); Costing and Purchasing name their suppliers here.
"""

import re
from pathlib import Path
from typing import Any

from p42cloud.sdk import (
    Boolean,
    Change,
    Choice,
    Client,
    Currency,
    Date,
    Email,
    FieldError,
    Flow,
    FormSection,
    FormView,
    Grant,
    Integer,
    Item,
    ListView,
    Module,
    Money,
    Phone,
    Problem,
    Reference,
    RelatedList,
    Role,
    Section,
    String,
    Table,
    TableHook,
    Text,
    Transition,
    Url,
    package_version,
)

ALL = ("read", "create", "write", "delete", "restore")

PARTNER = Table(
    "partner",
    "Partner",
    {
        "name": String("Name", max_length=160, required=True),
        "kind": Choice(
            "Kind", {"company": "Company", "person": "Person"}, required=True, default="company"
        ),
        "supplier": Boolean("Supplier", required=True, default=False),
        "customer": Boolean("Customer", required=True, default=False),
        "tenant": Boolean("Tenant", required=True, default=False),
        "email": Email("Email", personal=True),
        "phone": Phone("Phone", personal=True),
        "website": Url("Website"),
        "street": String("Street", max_length=160, personal=True),
        "postal_code": String("Postal code", max_length=20, personal=True),
        "city": String("City", max_length=100, personal=True),
        "country": String("Country", max_length=2, default="CH"),
        "vat_number": String("VAT number", max_length=40),
        "iban": String("IBAN", max_length=42, personal=True),
        "active": Boolean("Active", required=True, default=True),
        "notes": Text("Notes"),
    },
    label_plural="Partners",
    display="name",
    files=True,
    comments=True,
)

CONTACT = Table(
    "contact",
    "Contact person",
    {
        "partner": Reference("Partner", "partners.partner", required=True, on_delete="cascade"),
        "name": String("Name", max_length=160, required=True, personal=True),
        "role": String("Role", max_length=80),
        "email": Email("Email", personal=True),
        "phone": Phone("Phone", personal=True),
        "main": Boolean("Main contact", required=True, default=False),
    },
    label_plural="Contact persons",
    display="name",
)

PERIODS = {"month": "Monthly", "quarter": "Quarterly", "year": "Yearly", "once": "Once"}

CONTRACT = Table(
    "contract",
    "Contract",
    {
        "partner": Reference("Partner", "partners.partner", required=True, on_delete="restrict"),
        "name": String("Name", max_length=160, required=True),
        "kind": Choice(
            "Kind",
            {
                "lease": "Lease",
                "service": "Service",
                "supply": "Supply",
                "insurance": "Insurance",
                "other": "Other",
            },
            required=True,
            default="service",
        ),
        "state": Choice(
            "State",
            {"draft": "Draft", "active": "Active", "ended": "Ended"},
            required=True,
            default="draft",
            tones={"active": "primary", "ended": "tertiary"},
        ),
        "start": Date("Start"),
        "end": Date("End"),
        "notice_months": Integer("Notice (months)"),
        "currency": Currency("Currency", required=True, default="CHF"),
        "amount": Money("Amount", currency_field="currency"),
        "period": Choice("Every", PERIODS, required=True, default="month"),
        # A lease: what the tenant pays ahead towards the running costs, each period.
        "advance": Money("Advance for running costs", currency_field="currency"),
        "description": Text("What it covers"),
    },
    label_plural="Contracts",
    display="name",
    files=True,
    comments=True,
)

TABLES = (PARTNER, CONTACT, CONTRACT)

MANAGER = Role(
    "partners.manager",
    "Partner manager",
    grants=tuple(Grant(f"partners.{t.name}", ALL) for t in TABLES),
    description="Keeps partners, their contact persons and contracts.",
)
READER = Role(
    "partners.reader",
    "Partner reader",
    grants=tuple(Grant(f"partners.{t.name}", ("read",)) for t in TABLES),
    description="Reads partners and contracts: to book and plan with them.",
)


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(field, "invalid", message)],
    )


def iban_ok(given: str) -> bool:
    """An IBAN's check digits hold (ISO 13616: mod 97 of the rearranged number is 1)."""
    compact = re.sub(r"\s+", "", given).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        return False
    moved = compact[4:] + compact[:4]
    digits = "".join(str(int(c, 36)) for c in moved)
    return int(digits) % 97 == 1


async def _current(change: Change, table: str) -> dict[str, Any]:
    if change.operation == "write" and change.sys_id is not None:
        return await Client.of(change).get(table, change.sys_id)
    return {}


async def _check_partner(change: Change) -> None:
    """An IBAN with check digits that hold, kept in groups of four; a country as its code."""
    if change.operation in ("delete", "restore"):
        return
    values = change.values
    iban = values.get("iban")
    if isinstance(iban, str) and iban.strip():
        if not iban_ok(iban):
            raise _invalid("iban", "Not a valid IBAN: check the digits.")
        compact = re.sub(r"\s+", "", iban).upper()
        values["iban"] = " ".join(compact[i : i + 4] for i in range(0, len(compact), 4))
    country = values.get("country")
    if isinstance(country, str) and country:
        if not re.fullmatch(r"[A-Za-z]{2}", country.strip()):
            raise _invalid("country", "A country as its two letters, like CH.")
        values["country"] = country.strip().upper()


async def _check_contract(change: Change) -> None:
    """The end comes after the start; notice isn't negative."""
    if change.operation in ("delete", "restore"):
        return
    merged = await _current(change, "partners.contract") | change.values
    start, end = merged.get("start"), merged.get("end")
    if start and end and str(end) < str(start):
        raise _invalid("end", "The end comes after the start.")
    notice = merged.get("notice_months")
    if notice is not None and int(notice) < 0:
        raise _invalid("notice_months", "Notice isn't negative.")


module = Module(
    id="partners",
    name="Partners",
    publisher="p42",
    version=package_version("p42cloud-partners"),
    depends_on={"sys": ">=0.46,<1"},
    tables=TABLES,
    roles=(MANAGER, READER),
    hooks=(
        TableHook("partners.partner", before=_check_partner),
        TableHook("partners.contract", before=_check_contract),
    ),
    flows=(
        Flow(
            "partners.contract",
            "state",
            initial="draft",
            transitions=(
                Transition("activate", "Put in force", "draft", "active", icon="play_arrow"),
                Transition(
                    "end_contract",
                    "End",
                    "active",
                    "ended",
                    icon="stop",
                    confirm="End the contract? It stays on record.",
                ),
            ),
        ),
    ),
    docs=Path(__file__).parent / "docs",
    translations=Path(__file__).parent / "translations",
    description="Suppliers, customers and tenants, their contact persons and contracts.",
    category="Partners",
    icon="handshake",
    views=(
        ListView(
            "partners.partner",
            ("name", "kind", "supplier", "customer", "tenant", "city", "email", "active"),
            sort=("name",),
            search=("name", "city", "email"),
        ),
        ListView(
            "partners.contact",
            ("name", "partner", "role", "email", "phone", "main"),
            sort=("name",),
            search=("name", "email"),
        ),
        ListView(
            "partners.contract",
            ("name", "partner", "kind", "state", "start", "end", "amount", "period"),
            sort=("name",),
            search=("name",),
        ),
    ),
    forms=(
        FormView(
            "partners.partner",
            (
                FormSection(
                    "Partner", ("name", "kind", "supplier", "customer", "tenant", "active")
                ),
                FormSection("Contact", ("email", "phone", "website")),
                FormSection("Address", ("street", "postal_code", "city", "country")),
                FormSection("Payment and tax", ("iban", "vat_number")),
                FormSection("Notes", ("notes",), columns=1),
            ),
            related=(
                RelatedList("partners.contact", "partner"),
                RelatedList("partners.contract", "partner"),
            ),
        ),
        FormView(
            "partners.contact",
            (FormSection("Contact person", ("partner", "name", "role", "email", "phone", "main")),),
        ),
        FormView(
            "partners.contract",
            (
                FormSection("Contract", ("partner", "name", "kind", "state")),
                FormSection("Period", ("start", "end", "notice_months")),
                FormSection("Amounts", ("currency", "amount", "period", "advance")),
                FormSection("What it covers", ("description",), columns=1),
            ),
        ),
    ),
    navigation=(
        Section(
            "Partners",
            (
                Item("partners", "Partners", "partners.partner", icon="handshake"),
                Item("contacts", "Contact persons", "partners.contact", icon="contacts"),
                Item("contracts", "Contracts", "partners.contract", icon="contract"),
            ),
        ),
    ),
)
