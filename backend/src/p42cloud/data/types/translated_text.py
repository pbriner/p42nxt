"""Text in several languages, shown in the person's language and falling back to English."""

import re
from typing import Any, Unpack

from p42cloud.data.sql import ident
from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare

LANGUAGE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")


class TranslatedTextType(FieldType):
    name = "translated_text"
    label = "Translated text"
    kind = "translated"

    def column_type(self, options: dict[str, Any]) -> str:
        return "jsonb"

    def checks(self, column: str, options: dict[str, Any]) -> list[str]:
        return [f"jsonb_typeof({ident(column)}) = 'object'"]

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            value = {"en": value}
        if not isinstance(value, dict) or not value:
            raise Invalid('Must be a text per language, like {"en": "Desk"}.')
        texts: dict[str, str] = {}
        for language, text in value.items():  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(language, str) or not LANGUAGE.match(language):
                raise Invalid(f"Not a language code: {language!r}.")
            if not isinstance(text, str):
                raise Invalid("Every translation must be text.")
            texts[language] = text
        return texts


TYPE = TranslatedTextType()


def TranslatedText(label: str, **common: Unpack[Common]) -> Field:
    return declare("translated_text", label, {}, common)
