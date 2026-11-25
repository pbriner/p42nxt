"""A comment on a record, as a record."""

from p42cloud.data.declarations import Table
from p42cloud.data.types.any_record import AnyRecord
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text

KINDS = {"comment": "Comment", "work_note": "Work note"}

COMMENT = Table(
    "comment",
    "Comment",
    {
        "record_table": String("On table", max_length=80, required=True),
        "record_id": String("On record", max_length=36, required=True),
        "about": AnyRecord("On", table_field="record_table", read_only=True),
        "text": Text("Text", required=True),
        # A comment is read by everyone who may read the record, a work note only by those who
        # may change it.
        "kind": Choice("Kind", KINDS, required=True, default="comment"),
    },
    label_plural="Comments",
    display="text",
)

TABLES = [COMMENT]
