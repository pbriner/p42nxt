"""Languages and translations as records."""

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text

LANGUAGE = Table(
    "language",
    "Language",
    {
        "code": String("Code", max_length=5, required=True, unique=True),
        "name": String("Name", max_length=60, required=True),
        "offered": Boolean("Offered", required=True, default=False),
    },
    label_plural="Languages",
    display="name",
)

ORIGINS = {"module": "The module", "installation": "This installation"}

TRANSLATION = Table(
    "translation",
    "Translation",
    {
        "language": String("Language", max_length=5, required=True),
        # <table>|<natural id>|<field>: what's translated, e.g. sys.field|dca.plan.amount|label.
        "key": String("Key", max_length=300, required=True),
        "text": Text("Translation", required=True),
        # The English it was made from: when the English changes, it's outdated.
        "english": Text("English", required=True),
        "origin": Choice("From", ORIGINS, required=True, default="installation"),
        "module": String("Module", max_length=16),
    },
    label_plural="Translations",
    display="key",
    unique_together=(("language", "key", "origin"),),
)

TABLES = [LANGUAGE, TRANSLATION]
