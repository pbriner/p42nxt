# Languages

The app's texts come in English and German. Texts that modules define (table and field names,
menus, actions, widgets, roles, notification kinds) are written in English and translated per
language; everything else a person types stays as typed.

## Tables

| Table | Holds |
|---|---|
| `sys.language` | A language: `code` (`de`, `fr-CH`), `name` (in that language), `offered` |
| `sys.translation` | One translated text: `language`, `key`, `text`, `english` (the English it was made from), `origin` (`module` or `installation`), `module` |

Everyone may read the languages; administrators manage both tables (Platform → **Languages**,
**Translations**). Every installation starts with English and German.

## Keys

A key names one text: `<table>|<natural ID>|<field>`.

| Example | Text |
|---|---|
| `sys.table|dca.plan|label` | A table's name |
| `sys.field|dca.plan.amount|label` | A field's name (also `help`) |
| `sys.nav_item|dca.plans|label` | A menu entry; other definitions likewise by their name |

## How translations are shown

Modules write their texts in English on every install and update. Afterwards the translations
in force are put onto those texts: this installation's own over the module's. A translation
counts only while the English it was made from is still the English; when a module changes a
text, its translations are **outdated** and the English shows until they're translated again.
A text in the person's language shows, else English.

A module may ship translations in its `translations` folder, one `<language>.json` per language
(`modules.md`). The core and the DCA module ship German.

## Export and import

| Route | Who | Does |
|---|---|---|
| `GET /api/v1/languages` | Anyone | The languages: code, name, offered |
| `GET /api/v1/languages/{code}/export?module=…` | Administrators | Every text in English, with its translation and state |
| `POST /api/v1/languages/{code}/import` | Administrators | This installation's translations from such a file |

The file:

```json
{
  "language": "de",
  "module": "dca",
  "texts": [
    {"key": "sys.table|dca.plan|label", "english": "DCA plan", "text": "DCA-Plan", "state": "done"}
  ]
}
```

`state` is `missing`, `done` or `outdated` (export only). An import:

- is all or nothing: one wrong text refuses the file, saying which (422);
- takes only known keys, plain text up to 2000 characters, with the same `{placeholders}` as
  the English; at most 20,000 texts and 5 MB;
- treats an empty text as removing this installation's translation (the module's shows again,
  if it has one);
- answers how many texts were added, changed, removed and unchanged, and how many are shown.

Imported translations stay over module updates. In the app, a language's form has **Download
texts** and **Load translations**.

## Editing a translated text

A translated field shows English and the person's own language; the other languages are behind
**Other languages**.
