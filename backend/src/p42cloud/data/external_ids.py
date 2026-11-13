"""External IDs: how outside systems name our records (often called a correlation ID).

One record can carry IDs from many systems (a person from HR, a sign-in provider, payroll); each
system's ID is unique per table. Imports, integrations and sign-in providers all use this one
table, so a record loaded one way is found the same way later.
"""

from p42cloud.data.declarations import Table
from p42cloud.data.types.any_record import AnyRecord
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String

EXTERNAL_ID = Table(
    "external_id",
    "External ID",
    {
        "system": String("System", max_length=80, required=True),
        "external": String("ID there", max_length=255, required=True),
        "table": Reference("Table", "sys.table", required=True),
        "record": AnyRecord("Record", required=True),
    },
    label_plural="External IDs",
    unique_together=(("system", "table", "external"),),
)
