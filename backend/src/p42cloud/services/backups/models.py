"""Backups as records: every file in the backup folder, as the worker last saw it."""

from p42cloud.data.declarations import Table
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.decimal import Decimal
from p42cloud.data.types.string import String

BACKUP = Table(
    "backup",
    "Backup",
    {
        "file": String("File", max_length=120, required=True),
        "folder": String("Folder", max_length=500, required=True),
        "made": DateTime("Made", required=True),
        "size": Decimal("Size (MB)", precision=12, scale=1, required=True),
        "kind": Choice(
            "Kind",
            {"daily": "Daily", "before_update": "Before an update"},
            required=True,
            default="daily",
        ),
    },
    label_plural="Backups",
    unique_together=(("folder", "file"),),
)

TABLES = [BACKUP]
