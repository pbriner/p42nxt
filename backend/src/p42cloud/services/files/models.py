"""The record describing an attached file; its content is apart (sys.file_data), sealed."""

from p42cloud.data.declarations import Table
from p42cloud.data.types.any_record import AnyRecord
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.string import String

FILE = Table(
    "file",
    "File",
    {
        "name": String("Name", max_length=200, required=True),
        "type": String("Type", max_length=100, required=True),
        "size": Integer("Size (bytes)", required=True),
        "sha256": String("Checksum", max_length=64, required=True),
        "record_table": String("Attached to table", max_length=80, required=True),
        "record_id": String("Attached to record", max_length=36, required=True),
        "about": AnyRecord("Attached to", table_field="record_table", read_only=True),
        # The sealed content (sys.file_data), never shown.
        "content": String("Content", max_length=36, required=True),
    },
    label_plural="Files",
    display="name",
)

TABLES = [FILE]
