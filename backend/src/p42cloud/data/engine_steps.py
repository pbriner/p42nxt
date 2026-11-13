"""The engine's own database objects, as numbered steps applied once each by `p42 upgrade`.

These are the rules every table follows, enforced by the database itself so they hold for every
way in, SQL outside the app included: system fields filled and protected, no real deletes, every
change in the audit log, the audit log append-only and hash-chained. Tables themselves come
from declarations, not from here.

A released step is never changed; a fix is a new step.
"""

from typing import Final

# Who did something when no context says so: SQL outside the app. Fixed, like SYSTEM_PERSON.
DATABASE_PERSON: Final = "00000000-0000-7000-8000-000000000002"

# Every table's system fields. The record trigger fills them; nobody else may.
SYSTEM_FIELDS: Final = (
    "sys_id",
    "created",
    "created_by",
    "updated",
    "updated_by",
    "source",
    "deleted",
    "deleted_by",
)
SYSTEM_COLUMNS: Final = """
    sys_id uuid PRIMARY KEY DEFAULT uuidv7(),
    created timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated timestamptz NOT NULL,
    updated_by uuid NOT NULL,
    source text NOT NULL,
    deleted timestamptz,
    deleted_by uuid
"""

_CONTEXT = f"""
CREATE FUNCTION sys.p42_person() RETURNS uuid LANGUAGE sql STABLE AS $$
    SELECT coalesce(nullif(current_setting('p42.person_id', true), '')::uuid,
                    '{DATABASE_PERSON}'::uuid)
$$;

CREATE FUNCTION sys.p42_channel() RETURNS text LANGUAGE sql STABLE AS $$
    SELECT coalesce(nullif(current_setting('p42.channel', true), ''), 'db:' || session_user)
$$;

-- One request ID per transaction, also for SQL outside the app.
CREATE FUNCTION sys.p42_request() RETURNS uuid LANGUAGE plpgsql VOLATILE AS $$
DECLARE
    id text := nullif(current_setting('p42.request_id', true), '');
BEGIN
    IF id IS NULL THEN
        id := uuidv7()::text;
        PERFORM set_config('p42.request_id', id, true);
    END IF;
    RETURN id::uuid;
END $$;
"""

_RECORD_RULES = """
CREATE FUNCTION sys.p42_record_fields() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    person uuid := sys.p42_person();
    system_fields text[] := ARRAY['sys_id', 'created', 'created_by', 'updated', 'updated_by',
                                  'source', 'deleted', 'deleted_by'];
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created := now();
        NEW.created_by := person;
        NEW.updated := NEW.created;
        NEW.updated_by := person;
        NEW.source := sys.p42_channel();
        NEW.deleted := NULL;
        NEW.deleted_by := NULL;
        RETURN NEW;
    END IF;
    IF NEW.sys_id IS DISTINCT FROM OLD.sys_id OR NEW.created IS DISTINCT FROM OLD.created
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.source IS DISTINCT FROM OLD.source THEN
        RAISE EXCEPTION 'sys_id, created, created_by and source never change'
            USING ERRCODE = 'P4201';
    END IF;
    IF OLD.deleted IS NULL AND NEW.deleted IS NOT NULL THEN
        NEW.deleted := now();
        NEW.deleted_by := person;
    ELSIF OLD.deleted IS NOT NULL AND NEW.deleted IS NULL THEN
        NEW.deleted_by := NULL;
    ELSE
        NEW.deleted := OLD.deleted;
        NEW.deleted_by := OLD.deleted_by;
        IF to_jsonb(NEW) - system_fields = to_jsonb(OLD) - system_fields THEN
            -- Nothing changed, so the audit log gets no entry: updated_by must not name
            -- someone the log can't account for.
            NEW.updated := OLD.updated;
            NEW.updated_by := OLD.updated_by;
            RETURN NEW;
        END IF;
    END IF;
    NEW.updated := now();
    NEW.updated_by := person;
    RETURN NEW;
END $$;

CREATE FUNCTION sys.p42_no_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'records are never deleted; set deleted instead' USING ERRCODE = 'P4202';
END $$;
"""

_AUDIT_LOG = """
CREATE TABLE sys.audit_log (
    sys_id uuid NOT NULL DEFAULT uuidv7(),
    created timestamptz NOT NULL DEFAULT now(),
    table_name text,
    record_id uuid,
    operation text NOT NULL,
    person_id uuid NOT NULL,
    on_behalf_of uuid,
    channel text NOT NULL,
    request_id uuid NOT NULL,
    changes jsonb NOT NULL DEFAULT '{}',
    seq bigint,
    chain_hash bytea,
    PRIMARY KEY (sys_id),
    CHECK ((table_name IS NULL) = (record_id IS NULL))
) PARTITION BY RANGE (sys_id);
CREATE INDEX audit_log_record ON sys.audit_log (table_name, record_id, sys_id);
CREATE INDEX audit_log_request ON sys.audit_log (request_id);
CREATE INDEX audit_log_seq ON sys.audit_log (seq);

-- Months are ranges of sys_id: a UUIDv7 starts with its time, so the smallest one of a month
-- bounds it. Months are UTC, whatever the session's time zone, so bounds always line up.
CREATE FUNCTION sys.p42_uuidv7_floor(ts timestamptz) RETURNS uuid LANGUAGE sql IMMUTABLE AS $$
    SELECT (lpad(to_hex((extract(epoch FROM ts) * 1000)::bigint), 12, '0')
            || '70008000000000000000')::uuid
$$;

CREATE FUNCTION sys.p42_create_audit_partitions(first_month date, last_month date)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    month date := date_trunc('month', first_month);
BEGIN
    WHILE month <= last_month LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS sys.%I PARTITION OF sys.audit_log'
            ' FOR VALUES FROM (%L) TO (%L)',
            'audit_log_' || to_char(month, 'YYYY_MM'),
            sys.p42_uuidv7_floor(month::timestamp AT TIME ZONE 'UTC'),
            sys.p42_uuidv7_floor((month + interval '1 month')::timestamp AT TIME ZONE 'UTC'));
        month := month + interval '1 month';
    END LOOP;
END $$;
REVOKE ALL ON FUNCTION sys.p42_create_audit_partitions(date, date) FROM PUBLIC;
"""

_AUDIT_TRIGGER = """
-- Written with the owner's rights, so no module role needs to be able to write the log.
CREATE FUNCTION sys.p42_audit() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, sys AS $$
DECLARE
    old_row jsonb := CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
    new_row jsonb := to_jsonb(NEW);
    changes jsonb := '{}'::jsonb;
    operation text;
    key text;
    system_fields text[] := ARRAY['sys_id', 'created', 'created_by', 'updated', 'updated_by',
                                  'source', 'deleted', 'deleted_by'];
BEGIN
    IF TG_OP = 'INSERT' THEN
        operation := 'insert';
    ELSIF OLD.deleted IS NULL AND NEW.deleted IS NOT NULL THEN
        operation := 'delete';
    ELSIF OLD.deleted IS NOT NULL AND NEW.deleted IS NULL THEN
        operation := 'restore';
    ELSE
        operation := 'update';
    END IF;
    FOR key IN SELECT jsonb_object_keys(new_row) LOOP
        CONTINUE WHEN key = ANY (system_fields);
        CONTINUE WHEN TG_OP = 'INSERT' AND new_row -> key = 'null'::jsonb;
        CONTINUE WHEN old_row -> key IS NOT DISTINCT FROM new_row -> key;
        IF key = ANY (TG_ARGV) THEN
            -- A secret: that it changed, never its values.
            changes := changes || jsonb_build_object(key, '(changed)');
        ELSE
            changes := changes || jsonb_build_object(
                key, jsonb_build_array(old_row -> key, new_row -> key));
        END IF;
    END LOOP;
    IF operation = 'update' AND changes = '{}'::jsonb THEN
        RETURN NULL;  -- nothing changed that anyone could see
    END IF;
    INSERT INTO sys.audit_log
        (table_name, record_id, operation, person_id, channel, request_id, changes)
    VALUES (TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME, NEW.sys_id, operation,
            sys.p42_person(), sys.p42_channel(), sys.p42_request(), changes);
    RETURN NULL;
END $$;
-- Triggers fire without EXECUTE; this only stops anyone else attaching it to a table.
REVOKE ALL ON FUNCTION sys.p42_audit() FROM PUBLIC;

-- Puts the rules on a table: called by the engine for every table it builds.
CREATE FUNCTION sys.p42_attach_record_rules(target regclass, secrets text[] DEFAULT '{}')
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    arguments text := coalesce(
        (SELECT string_agg(quote_literal(s), ', ') FROM unnest(secrets) AS s), '');
BEGIN
    EXECUTE format('CREATE TRIGGER p42_record_fields BEFORE INSERT OR UPDATE ON %s'
                   ' FOR EACH ROW EXECUTE FUNCTION sys.p42_record_fields()', target);
    EXECUTE format('CREATE TRIGGER p42_no_delete BEFORE DELETE ON %s'
                   ' FOR EACH ROW EXECUTE FUNCTION sys.p42_no_delete()', target);
    EXECUTE format('CREATE TRIGGER p42_no_truncate BEFORE TRUNCATE ON %s'
                   ' FOR EACH STATEMENT EXECUTE FUNCTION sys.p42_no_delete()', target);
    EXECUTE format('CREATE TRIGGER p42_audit AFTER INSERT OR UPDATE ON %s'
                   ' FOR EACH ROW EXECUTE FUNCTION sys.p42_audit(%s)', target, arguments);
END $$;
REVOKE ALL ON FUNCTION sys.p42_attach_record_rules(regclass, text[]) FROM PUBLIC;
"""

# The hash of each entry covers the previous hash and every field of the entry, each
# length-prefixed so no two different entries read the same. verify_chain (audit.py) computes
# the same in Python.
_CHAIN = """
CREATE TABLE sys.audit_chain (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    seq bigint NOT NULL DEFAULT 0,
    hash bytea NOT NULL DEFAULT ''::bytea,
    pruned_seq bigint NOT NULL DEFAULT 0,
    pruned_hash bytea NOT NULL DEFAULT ''::bytea
);
INSERT INTO sys.audit_chain DEFAULT VALUES;

CREATE FUNCTION sys.p42_part(value text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN value IS NULL THEN '-'
                ELSE octet_length(convert_to(value, 'UTF8')) || ':' || value END
$$;

-- Runs at commit (a deferred trigger), so a transaction holds the chain's head only while it
-- commits, and entries get their numbers in the order transactions commit.
CREATE FUNCTION sys.p42_audit_seal() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, sys AS $$
DECLARE
    head sys.audit_chain;
    next_seq bigint;
    next_hash bytea;
BEGIN
    SELECT * INTO head FROM sys.audit_chain FOR UPDATE;
    next_seq := head.seq + 1;
    next_hash := sha256(head.hash || convert_to(
        sys.p42_part(next_seq::text) || sys.p42_part(NEW.sys_id::text)
        || sys.p42_part(((extract(epoch FROM NEW.created) * 1000000)::bigint)::text)
        || sys.p42_part(NEW.table_name) || sys.p42_part(NEW.record_id::text)
        || sys.p42_part(NEW.operation) || sys.p42_part(NEW.person_id::text)
        || sys.p42_part(NEW.on_behalf_of::text) || sys.p42_part(NEW.channel)
        || sys.p42_part(NEW.request_id::text) || sys.p42_part(NEW.changes::text), 'UTF8'));
    UPDATE sys.audit_log SET seq = next_seq, chain_hash = next_hash WHERE sys_id = NEW.sys_id;
    UPDATE sys.audit_chain SET seq = next_seq, hash = next_hash;
    RETURN NULL;
END $$;

REVOKE ALL ON FUNCTION sys.p42_audit_seal() FROM PUBLIC;

CREATE CONSTRAINT TRIGGER p42_audit_seal AFTER INSERT ON sys.audit_log
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION sys.p42_audit_seal();

-- Append-only: the only change ever allowed is sealing an entry once.
CREATE FUNCTION sys.p42_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.seq IS NULL AND NEW.seq IS NOT NULL
       AND to_jsonb(NEW) - 'seq' - 'chain_hash' = to_jsonb(OLD) - 'seq' - 'chain_hash' THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'the audit log can only be added to' USING ERRCODE = 'P4203';
END $$;
CREATE TRIGGER p42_append_only BEFORE UPDATE OR DELETE ON sys.audit_log
    FOR EACH ROW EXECUTE FUNCTION sys.p42_audit_append_only();
CREATE FUNCTION sys.p42_no_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'the audit log can only be added to' USING ERRCODE = 'P4203';
END $$;
CREATE TRIGGER p42_no_truncate BEFORE TRUNCATE ON sys.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION sys.p42_no_truncate();
CREATE TRIGGER p42_no_truncate BEFORE TRUNCATE ON sys.audit_chain
    FOR EACH STATEMENT EXECUTE FUNCTION sys.p42_no_truncate();

GRANT SELECT ON sys.audit_log, sys.audit_chain TO p42_mod_sys;
"""

# Every process keeps the metadata in memory; this tells them all when it changed.
_METADATA_CHANGED = """
CREATE FUNCTION sys.p42_metadata_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('p42_metadata', TG_TABLE_NAME);
    RETURN NULL;
END $$;
"""

# Text filters ignore case and accents (`muller` finds `Müller`). A trusted extension, so the
# owner can add it without an administrator.
_UNACCENT = """
CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA sys;
"""

# Personal records (access.py): whether a record is shared with a person, and whose a record is.
# With the owner's rights, so module roles needn't read the shares; the names in the dynamic SQL
# come from the metadata, never from the caller.
_PERSONAL = """
CREATE FUNCTION sys.p42_shared(in_table text, in_record uuid, in_person uuid, in_edit boolean)
RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, sys AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 FROM sys.share s JOIN sys."table" t ON t.sys_id = s."table"
        WHERE t."schema" || '.' || t.name = in_table AND s.record = in_record
          AND s.person = in_person AND s.deleted IS NULL
          AND (s.until IS NULL OR s.until > now()) AND (s.edit OR NOT in_edit));
END $$;

CREATE FUNCTION sys.p42_owner_of(in_table text, in_record uuid)
RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, sys AS $$
DECLARE
    target record;
    owner_id uuid;
BEGIN
    SELECT "schema", name, owner_field INTO target FROM sys."table"
    WHERE "schema" || '.' || name = in_table AND deleted IS NULL AND owner_field IS NOT NULL;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    EXECUTE format('SELECT %I FROM %I.%I WHERE sys_id = $1',
                   target.owner_field, target."schema", target.name)
        INTO owner_id USING in_record;
    RETURN owner_id;
END $$;
REVOKE ALL ON FUNCTION sys.p42_shared(text, uuid, uuid, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sys.p42_shared(text, uuid, uuid, boolean) TO p42_mod_sys;
REVOKE ALL ON FUNCTION sys.p42_owner_of(text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sys.p42_owner_of(text, uuid) TO p42_mod_sys;
"""

# Security events (sign-ins, failures, sign-outs, credential changes): audit entries without a
# record, written by the core alone. The index finds recent failures, to slow guessing down.
_SECURITY_EVENTS = """
CREATE FUNCTION sys.p42_security_event(in_event text, in_details jsonb)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, sys AS $$
    INSERT INTO sys.audit_log (operation, person_id, channel, request_id, changes)
    VALUES (in_event, sys.p42_person(), sys.p42_channel(), sys.p42_request(),
            coalesce(in_details, '{}'::jsonb));
$$;
REVOKE ALL ON FUNCTION sys.p42_security_event(text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sys.p42_security_event(text, jsonb) TO p42_mod_sys;
CREATE INDEX audit_log_security ON sys.audit_log (operation, created) WHERE table_name IS NULL;
"""

STEPS: Final[tuple[tuple[str, str], ...]] = (
    ("0001_context", _CONTEXT),
    ("0002_record_rules", _RECORD_RULES),
    ("0003_audit_log", _AUDIT_LOG),
    ("0004_audit_trigger", _AUDIT_TRIGGER),
    ("0005_audit_chain", _CHAIN),
    ("0006_metadata_changed", _METADATA_CHANGED),
    ("0007_unaccent", _UNACCENT),
    ("0008_personal", _PERSONAL),
    ("0009_security_events", _SECURITY_EVENTS),
)
