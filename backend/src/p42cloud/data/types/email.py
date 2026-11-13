"""An email address: text with an @ and a domain, in lower case.

Lower case, so one address is one person: `Anna@x.ch` and `anna@x.ch` can't be two accounts.
"""

from typing import Unpack

from p42cloud.data.types.base import Common, Field, PatternType, declare


class EmailType(PatternType):
    name = "email"
    label = "Email address"
    pattern = r"[^@\s]+@[^@\s]+\.[^@\s]+"
    column = "text"
    message = "Must be an email address, like anna@example.com."

    def normalize(self, value: str) -> str:
        return value.strip().lower()


TYPE = EmailType()


def Email(label: str, **common: Unpack[Common]) -> Field:
    return declare("email", label, {}, common)
