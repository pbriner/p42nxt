"""What files add to the core: their table and the size limit."""

from typing import Any

from p42cloud.data.settings import Setting
from p42cloud.data.types import Invalid
from p42cloud.services.files import models

TABLES = models.TABLES
MAX_MB = 100


def _megabytes(given: Any) -> int:
    try:
        found = int(given)
    except (TypeError, ValueError) as error:
        raise Invalid(f"A whole number of megabytes, 1 to {MAX_MB}.") from error
    if not 1 <= found <= MAX_MB:
        raise Invalid(f"A whole number of megabytes, 1 to {MAX_MB}.")
    return found


LARGEST = Setting(
    "sys.file_max_mb",
    "Largest file (MB)",
    20,
    check=_megabytes,
    help="Files attached to records may be at most this large.",
)
SETTINGS = (LARGEST,)
