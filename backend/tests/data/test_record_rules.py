"""The rules every table follows, enforced by the database: system fields, no real deletes, the
audit log (append-only, hash-chained), and the engine steps that install them."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.audit import missing_months, verify_chain
from p42cloud.data.engine_steps import DATABASE_PERSON, STEPS, SYSTEM_COLUMNS
from p42cloud.data.upgrade import upgrade
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction, transaction

ANNA = uuid.UUID("01900000-0000-7000-8000-00000000a001")


@pytest.fixture(scope="module")
async def thing(upgraded: Settings) -> AsyncIterator[str]:
    """A table built the way the engine will build them: system fields and the rules."""
    owner = create_engine(upgraded.owner_database_url)
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            await conn.exec_driver_sql(
                f"CREATE TABLE sys.thing ({SYSTEM_COLUMNS}, name text, secret text)"
            )
            await conn.execute(text("SELECT sys.p42_attach_record_rules('sys.thing', '{secret}')"))
            await conn.execute(text("GRANT SELECT, INSERT, UPDATE ON sys.thing TO p42_mod_sys"))
    await owner.dispose()
    yield "sys.thing"


async def _insert(
    engine: AsyncEngine, context: Context, name: str, hidden: str | None = None
) -> uuid.UUID:
    with working_as(context):
        async with transaction(engine, "sys") as conn:
            row_id = await conn.scalar(
                text("INSERT INTO sys.thing (name, secret) VALUES (:n, :s) RETURNING sys_id"),
                {"n": name, "s": hidden},
            )
    assert isinstance(row_id, uuid.UUID)
    return row_id


async def _entries(engine: AsyncEngine, record: uuid.UUID) -> list[tuple[str, dict[str, object]]]:
    with working_as(Context(channel="test")):
        async with transaction(engine, "sys") as conn:
            rows = await conn.execute(
                text(
                    "SELECT operation, changes FROM sys.audit_log WHERE record_id = :r ORDER BY seq"
                ),
                {"r": record},
            )
            return [(op, changes) for op, changes in rows.all()]


async def test_upgrading_again_changes_nothing(owner_engine: AsyncEngine) -> None:
    with working_as(Context(channel="test")):
        assert await upgrade(owner_engine) == []
        async with owner_transaction(owner_engine) as conn:
            done = list((await conn.execute(text("SELECT id FROM sys.engine_step"))).scalars())
    assert sorted(done) == [step for step, _ in STEPS]


async def test_system_fields_come_from_the_context(app_engine: AsyncEngine, thing: str) -> None:
    context = Context(channel="api:token:sync", person_id=ANNA)
    record = await _insert(app_engine, context, name="Printer")
    with working_as(context):
        async with transaction(app_engine, "sys") as conn:
            row = (
                await conn.execute(
                    text("SELECT created_by, updated_by, source FROM sys.thing WHERE sys_id = :r"),
                    {"r": record},
                )
            ).one()
    assert tuple(row) == (ANNA, ANNA, "api:token:sync")


async def test_system_fields_can_not_be_set_or_changed(app_engine: AsyncEngine, thing: str) -> None:
    context = Context(channel="ui", person_id=ANNA)
    with working_as(context):
        async with transaction(app_engine, "sys") as conn:
            record = await conn.scalar(
                text(
                    "INSERT INTO sys.thing (name, created_by, source)"
                    " VALUES ('Desk', :someone, 'import') RETURNING sys_id"
                ),
                {"someone": uuid.uuid7()},
            )
            row = (
                await conn.execute(
                    text("SELECT created_by, source FROM sys.thing WHERE sys_id = :r"),
                    {"r": record},
                )
            ).one()
        assert tuple(row) == (ANNA, "ui")
        with pytest.raises(DBAPIError, match="never change"):
            async with transaction(app_engine, "sys") as conn:
                await conn.execute(
                    text("UPDATE sys.thing SET source = 'import' WHERE sys_id = :r"),
                    {"r": record},
                )


async def test_records_are_never_deleted(app_engine: AsyncEngine, thing: str) -> None:
    record = await _insert(app_engine, Context(channel="ui"), name="Chair")
    with working_as(Context(channel="ui")), pytest.raises(DBAPIError):
        async with transaction(app_engine, "sys") as conn:
            await conn.execute(text("DELETE FROM sys.thing WHERE sys_id = :r"), {"r": record})


async def test_every_change_is_audited_with_only_what_changed(
    app_engine: AsyncEngine, thing: str
) -> None:
    context = Context(channel="ui", person_id=ANNA)
    record = await _insert(app_engine, context, name="Lamp", hidden="hunter2")
    with working_as(context):
        for statement in (
            "UPDATE sys.thing SET name = 'Desk lamp' WHERE sys_id = :r",
            "UPDATE sys.thing SET name = 'Desk lamp' WHERE sys_id = :r",  # nothing changes
            "UPDATE sys.thing SET secret = 'correct horse' WHERE sys_id = :r",
            "UPDATE sys.thing SET deleted = now() WHERE sys_id = :r",
            "UPDATE sys.thing SET deleted = NULL WHERE sys_id = :r",
        ):
            async with transaction(app_engine, "sys") as conn:
                await conn.execute(text(statement), {"r": record})
    assert await _entries(app_engine, record) == [
        ("insert", {"name": [None, "Lamp"], "secret": "(changed)"}),
        ("update", {"name": ["Lamp", "Desk lamp"]}),
        ("update", {"secret": "(changed)"}),
        ("delete", {}),
        ("restore", {}),
    ]


async def test_soft_deleting_records_who_and_when(app_engine: AsyncEngine, thing: str) -> None:
    context = Context(channel="ui", person_id=ANNA)
    record = await _insert(app_engine, context, name="Shelf")
    with working_as(context):
        async with transaction(app_engine, "sys") as conn:
            await conn.execute(
                text("UPDATE sys.thing SET deleted = '2000-01-01' WHERE sys_id = :r"),
                {"r": record},
            )
            deleted, deleted_by = (
                await conn.execute(
                    text("SELECT deleted, deleted_by FROM sys.thing WHERE sys_id = :r"),
                    {"r": record},
                )
            ).one()
    assert deleted_by == ANNA
    assert deleted.year == datetime.now(UTC).year  # the time is the database's, not the caller's


async def test_sql_outside_the_app_is_audited_too(
    admin_conn: AsyncEngine, app_engine: AsyncEngine, thing: str
) -> None:
    async with admin_conn.connect() as conn:
        record = await conn.scalar(
            text("INSERT INTO sys.thing (name) VALUES ('By hand') RETURNING sys_id")
        )
    assert isinstance(record, uuid.UUID)
    with working_as(Context(channel="test")):
        async with transaction(app_engine, "sys") as conn:
            person, channel = (
                await conn.execute(
                    text("SELECT person_id, channel FROM sys.audit_log WHERE record_id = :r"),
                    {"r": record},
                )
            ).one()
    assert (str(person), channel) == (DATABASE_PERSON, "db:p42_admin")


async def test_the_audit_log_can_only_be_added_to(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, thing: str
) -> None:
    await _insert(app_engine, Context(channel="ui"), name="Box")
    with working_as(Context(channel="test")):
        with pytest.raises(DBAPIError):
            async with transaction(app_engine, "sys") as conn:
                await conn.execute(
                    text(
                        "INSERT INTO sys.audit_log (operation, person_id, channel, request_id)"
                        " VALUES ('forged', :p, 'ui', :p)"
                    ),
                    {"p": uuid.uuid7()},
                )
        for statement in ("UPDATE sys.audit_log SET channel = 'x'", "DELETE FROM sys.audit_log"):
            with pytest.raises(DBAPIError, match="only be added to"):
                async with owner_transaction(owner_engine) as conn:
                    await conn.execute(text(statement))


async def test_entries_are_sealed_in_order_and_the_chain_verifies(
    app_engine: AsyncEngine, thing: str
) -> None:
    for name in ("One", "Two", "Three"):
        await _insert(app_engine, Context(channel="ui"), name=name)
    with working_as(Context(channel="test")):
        async with transaction(app_engine, "sys") as conn:
            seqs = list(
                (await conn.execute(text("SELECT seq FROM sys.audit_log ORDER BY seq"))).scalars()
            )
            check = await verify_chain(conn)
    assert seqs == list(range(1, len(seqs) + 1))
    assert check.intact, check.problem
    assert check.entries == len(seqs)


async def test_tampering_is_found(
    admin_conn: AsyncEngine, app_engine: AsyncEngine, thing: str
) -> None:
    record = await _insert(app_engine, Context(channel="ui"), name="Original")
    # A database superuser can switch the triggers off; the chain still gives it away.
    async with admin_conn.connect() as conn:
        original = await conn.scalar(
            text("SELECT changes::text FROM sys.audit_log WHERE record_id = :r"), {"r": record}
        )
        await conn.exec_driver_sql("SET session_replication_role = replica")
        await conn.execute(
            text("UPDATE sys.audit_log SET changes = CAST(:forged AS jsonb) WHERE record_id = :r"),
            {"forged": '{"name": [null, "Forged"]}', "r": record},
        )
    try:
        with working_as(Context(channel="test")):
            async with transaction(app_engine, "sys") as conn:
                check = await verify_chain(conn)
        assert check.problem is not None
        assert "was changed" in check.problem
    finally:
        async with admin_conn.connect() as conn:
            await conn.exec_driver_sql("SET session_replication_role = replica")
            await conn.execute(
                text("UPDATE sys.audit_log SET changes = CAST(:c AS jsonb) WHERE record_id = :r"),
                {"c": original, "r": record},
            )


async def test_months_ahead_exist_and_missing_ones_are_found(
    app_engine: AsyncEngine, upgraded: object
) -> None:
    with working_as(Context(channel="test")):
        async with transaction(app_engine, "sys") as conn:
            assert await missing_months(conn) == []
            assert await missing_months(conn, today=datetime(2099, 1, 15, tzinfo=UTC)) == [
                "2099-01",
                "2099-02",
            ]
