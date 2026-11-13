"""Problems: errors as RFC 9457 problem details, the same for every caller.

`code` and the type link are stable and meant for programs; `title` and the field messages are
for people (translated once texts are records).
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FieldError:
    field: str
    code: str
    message: str


@dataclass
class Problem(Exception):
    status: int
    code: str
    title: str
    detail: str | None = None
    errors: list[FieldError] = field(default_factory=list[FieldError])
    # More for programs, merged into the answer (the current record on a stale ETag).
    extra: dict[str, Any] = field(default_factory=dict[str, Any])

    def __str__(self) -> str:
        return f"{self.status} {self.code}: {self.detail or self.title}"

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": f"/api/v1/problems/{self.code}",
            "title": self.title,
            "status": self.status,
            "code": self.code,
        }
        if self.detail:
            body["detail"] = self.detail
        if self.errors:
            body["errors"] = [
                {"field": e.field, "code": e.code, "message": e.message} for e in self.errors
            ]
        return {**self.extra, **body}
