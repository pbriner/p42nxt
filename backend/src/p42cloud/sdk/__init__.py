"""The SDK: everything a module may use of the platform, and the only thing it may import.

A module declares itself with `Module`, its tables with `Table` and the field types, its roles
with `Role` and `Grant`, reacts to its own records' changes with `TableHook`, moves data with
`Migration`, and reads and changes any module's records with `Client`. Refusals are `Problem`s.
"""

from p42cloud.data.declarations import Table
from p42cloud.data.hooks import Change, TableHook
from p42cloud.data.types.any_record import AnyRecord
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.color import Color
from p42cloud.data.types.date import Date
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.decimal import Decimal
from p42cloud.data.types.duration import Duration
from p42cloud.data.types.email import Email
from p42cloud.data.types.hostname import Hostname
from p42cloud.data.types.icon import Icon
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.json import Json
from p42cloud.data.types.markdown import Markdown
from p42cloud.data.types.percent import Percent
from p42cloud.data.types.phone import Phone
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.secret import Secret
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text
from p42cloud.data.types.translated_text import TranslatedText
from p42cloud.data.types.url import Url
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.modules.manifest import Migration, Module
from p42cloud.sdk.client import Client
from p42cloud.security.access.roles import Grant, Role

__all__ = [
    "AnyRecord",
    "Boolean",
    "Change",
    "Choice",
    "Client",
    "Color",
    "Date",
    "DateTime",
    "Decimal",
    "Duration",
    "Email",
    "FieldError",
    "Grant",
    "Hostname",
    "Icon",
    "Integer",
    "Json",
    "Markdown",
    "Migration",
    "Module",
    "Percent",
    "Phone",
    "Problem",
    "Reference",
    "Role",
    "Secret",
    "String",
    "Table",
    "TableHook",
    "Text",
    "TranslatedText",
    "Url",
]
