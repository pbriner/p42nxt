"""The engine that builds the database from the catalog: it compares and applies the difference.

Tables, columns, checks, references and unique indexes come from the metadata records and
nothing else. Things are only ever added or widened: a retired field keeps its column and data,
and a type changes only where every value fits the new one. Runs as the owner, in the same
transaction as the change of the records, so both change or neither does.

Constraints and indexes are named after a hash of their definition (`p42_<field>_<kind><hash>`):
a changed definition is a new name, so comparing names is enough to know what to drop and add.
"""

import hashlib
import re
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog, FieldDef, TableDef
from p42cloud.data.engine_steps import SYSTEM_COLUMNS
from p42cloud.data.sql import ident, literal, qualified
from p42cloud.kernel.db import module_role
from p42cloud.kernel.errors import Problem

# The metadata tables announce every change, so each process drops its cached catalog.
METADATA_TABLES = frozenset({"sys.table", "sys.field", "sys.field_type"})


@dataclass(frozen=True)
class Step:
    sql: str
    says: str  # what it does, in plain words, for the upgrade's output and for errors


@dataclass
class Existing:
    """What the database has for one table."""

    columns: dict[str, str] = field(default_factory=dict[str, str])
    constraints: set[str] = field(default_factory=set[str])
    indexes: set[str] = field(default_factory=set[str])
    triggers: dict[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])


@dataclass(frozen=True)
class Database:
    schemas: frozenset[str]
    roles: frozenset[str]
    tables: dict[str, Existing]
    # (module role, table) pairs that may read the table: for references into other modules.
    reads: frozenset[tuple[str, str]] = frozenset()


# ---- reading the database ----

_SCHEMAS = text("SELECT nspname FROM pg_namespace WHERE nspname = ANY(:schemas)")
_ROLES = text("SELECT rolname FROM pg_roles WHERE rolname LIKE 'p42\\_mod\\_%'")
_TABLES = text(
    "SELECT c.oid, n.nspname || '.' || c.relname AS key FROM pg_class c"
    " JOIN pg_namespace n ON n.oid = c.relnamespace"
    " WHERE c.relkind IN ('r', 'p') AND n.nspname = ANY(:schemas)"
)
_COLUMNS = text(
    "SELECT attrelid, attname, format_type(atttypid, atttypmod) AS type FROM pg_attribute"
    " WHERE attrelid = ANY(CAST(:oids AS oid[])) AND attnum > 0 AND NOT attisdropped"
)
_CONSTRAINTS = text(
    "SELECT conrelid, conname FROM pg_constraint"
    " WHERE conrelid = ANY(CAST(:oids AS oid[])) AND conname LIKE 'p42\\_%'"
)
_INDEXES = text(
    "SELECT i.indrelid, c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid"
    " WHERE i.indrelid = ANY(CAST(:oids AS oid[])) AND c.relname LIKE 'p42\\_%'"
)
_READS = text(
    "SELECT r.rolname, n.nspname || '.' || c.relname FROM pg_class c"
    " JOIN pg_namespace n ON n.oid = c.relnamespace"
    " CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid = a.grantee"
    " WHERE a.privilege_type = 'SELECT' AND n.nspname = ANY(:schemas)"
    " AND r.rolname LIKE 'p42\\_mod\\_%'"
)
_TRIGGERS = text(
    "SELECT tgrelid, tgname, tgargs FROM pg_trigger"
    " WHERE tgrelid = ANY(CAST(:oids AS oid[])) AND NOT tgisinternal"
)


def _arguments(raw: bytes) -> tuple[str, ...]:
    return tuple(a.decode() for a in raw.split(b"\0") if a)


async def read_database(conn: AsyncConnection, schemas: set[str]) -> Database:
    names = sorted(schemas)
    present = frozenset((await conn.execute(_SCHEMAS, {"schemas": names})).scalars())
    roles = frozenset((await conn.execute(_ROLES)).scalars())
    by_oid: dict[int, Existing] = {}
    tables: dict[str, Existing] = {}
    for oid, key in (await conn.execute(_TABLES, {"schemas": names})).all():
        tables[key] = by_oid[oid] = Existing()
    oids = {"oids": list(by_oid)}
    for oid, name, column_type in (await conn.execute(_COLUMNS, oids)).all():
        by_oid[oid].columns[name] = column_type
    for oid, name in (await conn.execute(_CONSTRAINTS, oids)).all():
        by_oid[oid].constraints.add(name)
    for oid, name in (await conn.execute(_INDEXES, oids)).all():
        by_oid[oid].indexes.add(name)
    for oid, name, args in (await conn.execute(_TRIGGERS, oids)).all():
        by_oid[oid].triggers[name] = _arguments(bytes(args))
    reads = frozenset(
        (role, key) for role, key in (await conn.execute(_READS, {"schemas": names})).all()
    )
    return Database(present, roles, tables, reads)


# ---- what the catalog asks for ----


def _name(table: TableDef, column: str, kind: str, definition: str) -> str:
    digest = hashlib.sha256(f"{table.key}|{definition}".encode()).hexdigest()[:8]
    return f"p42_{column}_{kind}{digest}"


def _target(catalog: Catalog, table: TableDef, f: FieldDef) -> TableDef | None:
    key = f.type.references(f.field.options)
    if key is None:
        return None
    target = catalog.table(key)
    if target is None or target.retired:
        raise Problem(
            422,
            "unknown_table",
            "A reference points to a table that doesn't exist.",
            detail=f"{table.key}.{f.name} refers to {key}.",
        )
    return target


def _wanted(catalog: Catalog, table: TableDef) -> tuple[dict[str, str], dict[str, str]]:
    """Constraints and indexes the table should have, by name, with their definitions."""
    name = qualified(table.schema, table.name)
    constraints: dict[str, str] = {}
    indexes: dict[str, str] = {}

    def constraint(column: str, kind: str, definition: str) -> None:
        constraints[_name(table, column, kind, definition)] = definition

    def index(columns: tuple[str, ...], kind: str, definition: str) -> None:
        index_name = _name(table, columns[0], kind, definition)
        indexes[index_name] = f"CREATE {definition.format(name=ident(index_name), table=name)}"

    for f in table.active_fields():
        column = ident(f.name)
        for expression in f.type.checks(f.name, f.field.options):
            constraint(f.name, "c", f"CHECK ({expression})")
        if f.field.required:
            constraint(f.name, "r", f"CHECK (deleted IS NOT NULL OR {column} IS NOT NULL)")
        target = _target(catalog, table, f)
        if target is not None:
            constraint(
                f.name,
                "f",
                f"FOREIGN KEY ({column}) REFERENCES {qualified(target.schema, target.name)}"
                " (sys_id)",
            )
            index((f.name,), "i", f"INDEX {{name}} ON {{table}} ({column})")
        if f.field.unique:
            index(
                (f.name,),
                "u",
                f"UNIQUE INDEX {{name}} ON {{table}} ({column}) WHERE deleted IS NULL",
            )
    active = {f.name for f in table.active_fields()}
    for together in table.unique_together:
        if set(together) <= active:
            columns = ", ".join(ident(c) for c in together)
            index(
                together,
                "u",
                f"UNIQUE INDEX {{name}} ON {{table}} ({columns}) WHERE deleted IS NULL",
            )
    return constraints, indexes


_VARCHAR = re.compile(r"^character varying\((\d+)\)$")
_NUMERIC = re.compile(r"^numeric\((\d+),(\d+)\)$")
_BIGINT_DIGITS = 19


def widens(old: str, new: str) -> bool:
    """Whether every value of column type `old` fits `new` unchanged."""
    if old == new or (_VARCHAR.match(old) and new == "text"):
        return True
    if (before := _VARCHAR.match(old)) and (after := _VARCHAR.match(new)):
        return int(before[1]) <= int(after[1])
    after = _NUMERIC.match(new)
    if after is None:
        return False
    precision, scale = int(after[1]), int(after[2])
    if old == "bigint":
        return precision - scale >= _BIGINT_DIGITS
    before = _NUMERIC.match(old)
    return (
        before is not None
        and scale >= int(before[2])
        and precision - scale >= int(before[1]) - int(before[2])
    )


def _secrets(table: TableDef) -> tuple[str, ...]:
    return tuple(f.name for f in table.fields if f.type.secret)


def _links(table: TableDef) -> tuple[str, ...]:
    """The link trigger's arguments: per reference filled from a key, four (reference.py)."""
    found: list[str] = []
    names = {f.name for f in table.active_fields()}
    for f in table.active_fields():
        key = f.field.options.get("key") if f.type.name == "reference" else None
        if key is None:
            continue
        if key not in names:
            raise Problem(
                409,
                "bad_link",
                "A reference filled from a key needs that field.",
                detail=f"{table.key}.{f.name} is filled from {key}, which it doesn't have.",
            )
        schema, name = str(f.field.options["table"]).split(".", 1)
        found += [f.name, key, qualified(schema, name), str(f.field.options["match"])]
    return tuple(found)


def _link_steps(table: TableDef, before: tuple[str, ...]) -> list[Step]:
    """The link trigger (again, if its links changed), and the links of the records there."""
    wanted = _links(table)
    if wanted == before:
        return []
    name = qualified(table.schema, table.name)
    steps = [Step(f"DROP TRIGGER IF EXISTS p42_link ON {name}", f"Stop linking {table.key}")]
    if not wanted:
        return steps
    arguments = ", ".join(literal(a) for a in wanted)
    steps.append(
        Step(
            f"CREATE TRIGGER p42_link BEFORE INSERT OR UPDATE ON {name}"
            f" FOR EACH ROW EXECUTE FUNCTION sys.p42_link({arguments})",
            f"Link {table.key}'s records by their keys",
        )
    )
    old = {before[i] for i in range(0, len(before), 4)}
    for i in range(0, len(wanted), 4):
        ref, key = wanted[i], wanted[i + 1]
        if ref in old:
            continue
        steps.append(
            Step(
                f"UPDATE {name} SET {ident(key)} = {ident(key)}"  # noqa: S608 - checked names only
                f" WHERE {ident(key)} IS NOT NULL AND {ident(ref)} IS NULL",
                f"Link the records of {table.key} there already ({ref})",
            )
        )
    return steps


def _create(table: TableDef, wanted_role: str) -> list[Step]:
    name = qualified(table.schema, table.name)
    columns = [SYSTEM_COLUMNS.strip()] + [
        f"{ident(f.name)} {f.type.column_type(f.field.options)}" for f in table.active_fields()
    ]
    secrets = ", ".join(literal(s) for s in _secrets(table))
    steps = [
        Step(f"CREATE TABLE {name} ({', '.join(columns)})", f"Create the table {table.key}"),
        Step(
            f"SELECT sys.p42_attach_record_rules({literal(name)}::regclass,"
            f" ARRAY[{secrets}]::text[])",
            f"Put the record rules on {table.key}",
        ),
        Step(
            f"GRANT SELECT, INSERT, UPDATE ON {name} TO {ident(wanted_role)}",
            f"Let the module {table.schema} write {table.key}",
        ),
    ]
    if table.key in METADATA_TABLES:
        steps.append(_announce(table))
    return steps + _link_steps(table, ())


def _announce(table: TableDef) -> Step:
    return Step(
        f"CREATE TRIGGER p42_announce AFTER INSERT OR UPDATE ON"
        f" {qualified(table.schema, table.name)}"
        " FOR EACH STATEMENT EXECUTE FUNCTION sys.p42_metadata_changed()",
        f"Announce changes of {table.key}",
    )


def _add_column(name: str, f: FieldDef, wanted: str, where: str) -> list[Step]:
    """A new column; with a default, the records already there get it (a new required field
    can't leave them empty). The column keeps no default of its own: defaults are the records
    service's."""
    column = ident(f.name)
    if f.field.default is None:
        return [Step(f"ALTER TABLE {name} ADD COLUMN {column} {wanted}", f"Add {where}")]
    value = f.type.sql_literal(f.field.default, f.field.options)
    return [
        Step(
            f"ALTER TABLE {name} ADD COLUMN {column} {wanted} DEFAULT {value}",
            f"Add {where}, with its default for the records already there",
        ),
        Step(f"ALTER TABLE {name} ALTER COLUMN {column} DROP DEFAULT", f"Add {where}"),
    ]


def _change(table: TableDef, existing: Existing) -> list[Step]:
    name = qualified(table.schema, table.name)
    steps: list[Step] = []
    for f in table.active_fields():
        wanted = f.type.column_type(f.field.options)
        have = existing.columns.get(f.name)
        where = f"{table.key}.{f.name}"
        if have is None:
            steps += _add_column(name, f, wanted, where)
        elif have != wanted:
            if not widens(have, wanted):
                raise Problem(
                    409,
                    "unsafe_change",
                    "A field's type can only change where no value is lost.",
                    detail=f"{where} is {have}; it can't become {wanted}.",
                )
            steps.append(
                Step(
                    f"ALTER TABLE {name} ALTER COLUMN {ident(f.name)} TYPE {wanted}"
                    f" USING {ident(f.name)}::{wanted}",
                    f"Widen {where} to {wanted}",
                )
            )
    secrets = _secrets(table)
    if existing.triggers.get("p42_audit", ()) != secrets:
        arguments = ", ".join(literal(s) for s in secrets)
        steps += [
            Step(f"DROP TRIGGER IF EXISTS p42_audit ON {name}", f"Stop auditing {table.key}"),
            Step(
                f"CREATE TRIGGER p42_audit AFTER INSERT OR UPDATE ON {name}"
                f" FOR EACH ROW EXECUTE FUNCTION sys.p42_audit({arguments})",
                f"Audit {table.key} again, its secret fields masked",
            ),
        ]
    if table.key in METADATA_TABLES and "p42_announce" not in existing.triggers:
        steps.append(_announce(table))
    return steps + _link_steps(table, existing.triggers.get("p42_link", ()))


def plan(catalog: Catalog, database: Database) -> list[Step]:
    """Everything to do, in order: schemas, tables and columns, then drops, then constraints."""
    building: list[Step] = []
    dropping: list[Step] = []
    adding: list[Step] = []
    for schema in sorted({t.schema for t in catalog.tables.values()} - database.schemas):
        role = module_role(schema)
        if role not in database.roles:
            raise Problem(
                409,
                "no_module_role",
                "The module has no database role yet.",
                detail=f"Set up the role {role} first (`p42cloud setup-database`).",
            )
        building += [
            Step(f"CREATE SCHEMA {ident(schema)}", f"Create the schema {schema}"),
            Step(f"GRANT USAGE ON SCHEMA {ident(schema)} TO {ident(role)}", f"Let {role} in"),
            # The record rules on its tables call functions in sys; it reads no table there.
            Step(f"GRANT USAGE ON SCHEMA sys TO {ident(role)}", f"Let {role} use the record rules"),
            Step(
                "GRANT EXECUTE ON FUNCTION sys.p42_shared(text, uuid, uuid, boolean)"
                f" TO {ident(role)}",
                f"Let {role} check shares of personal records",
            ),
        ]
    for table in sorted(catalog.tables.values(), key=lambda t: t.key):
        if table.retired:
            continue  # kept as it is, with its data
        existing = database.tables.get(table.key)
        if existing is None:
            building += _create(table, module_role(table.schema))
            existing = Existing()
        else:
            building += _change(table, existing)
        name = qualified(table.schema, table.name)
        constraints, indexes = _wanted(catalog, table)
        for old in sorted(existing.constraints - constraints.keys()):
            dropping.append(Step(f"ALTER TABLE {name} DROP CONSTRAINT {ident(old)}", f"Drop {old}"))
        for old in sorted(existing.indexes - indexes.keys()):
            dropping.append(Step(f"DROP INDEX {ident(table.schema)}.{ident(old)}", f"Drop {old}"))
        for new in sorted(constraints.keys() - existing.constraints):
            adding.append(
                Step(
                    f"ALTER TABLE {name} ADD CONSTRAINT {ident(new)} {constraints[new]}",
                    _says(table, new, constraints[new]),
                )
            )
        for new in sorted(indexes.keys() - existing.indexes):
            adding.append(Step(indexes[new], _says(table, new, indexes[new])))
    return building + dropping + adding + _reads(catalog, database)


def _reads(catalog: Catalog, database: Database) -> list[Step]:
    """Read rights for each module on the tables its references reach in other modules.

    A list filtered along a reference (`owner.name`) reads the target table as the module's own
    role. Which of those records and fields anyone gets is still decided by the grants, in SQL;
    a module's role still writes only its own schema.
    """
    links: dict[str, set[str]] = {}
    for table in catalog.tables.values():
        for f in table.active_fields():
            target = f.type.references(f.field.options)
            if target is not None and not table.retired and catalog.table(target) is not None:
                links.setdefault(table.key, set()).add(target)
    steps: list[Step] = []
    for schema in sorted({t.schema for t in catalog.tables.values()}):
        reached: set[str] = set()
        waiting = [t.key for t in catalog.tables.values() if t.schema == schema]
        while waiting:
            for target in links.get(waiting.pop(), set()) - reached:
                reached.add(target)
                waiting.append(target)
        role = module_role(schema)
        for key in sorted(reached):
            target = catalog.tables[key]
            if target.schema != schema and (role, key) not in database.reads:
                steps += [
                    Step(
                        f"GRANT USAGE ON SCHEMA {ident(target.schema)} TO {ident(role)}",
                        f"Let the module {schema} into {target.schema}",
                    ),
                    Step(
                        f"GRANT SELECT ON {qualified(target.schema, target.name)} TO {ident(role)}",
                        f"Let the module {schema} read {key} (it refers to it)",
                    ),
                ]
    return steps


def _says(table: TableDef, name: str, definition: str) -> str:
    column = name.removeprefix("p42_").rsplit("_", 1)[0]
    where = f"{table.key}.{column}"
    if "IS NOT NULL OR" in definition:
        return f"Make {where} required (no active record may leave it empty)"
    if definition.startswith("CREATE UNIQUE"):
        columns = re.search(r"\(([^()]*)\)", definition)
        names = [c.strip().strip('"') for c in columns[1].split(",")] if columns else [column]
        if len(names) > 1:  # unique together
            return f"Make {table.key}'s {' and '.join(names)} unique together among active records"
        return f"Make {where} unique among active records"
    if definition.startswith("FOREIGN KEY"):
        return f"Check that every {where} points to an existing record"
    if definition.startswith("CREATE INDEX"):
        return f"Index {where}"
    return f"Check the values of {where}"


async def apply(conn: AsyncConnection, catalog: Catalog) -> list[str]:
    """Brings the database to what the catalog says; returns what was done, in plain words."""
    database = await read_database(conn, {t.schema for t in catalog.tables.values()})
    done: list[str] = []
    for step in plan(catalog, database):
        try:
            await conn.exec_driver_sql(step.sql)
        except DBAPIError as error:
            raise Problem(
                409,
                "schema_change_failed",
                "The database refused a change.",
                detail=f"{step.says}: {error.orig}",
            ) from error
        done.append(step.says)
    return done
