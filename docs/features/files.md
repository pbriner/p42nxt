# Files

Files are attached to records: a receipt to a cost line, a contract to a partner. A table allows
it in its declaration; whoever may read a record reads its files, and whoever may change it
attaches and removes them.

## Allowing files

```python
Table("receipt", "Receipt", {...}, files=True)
```

The table's description in the API says `files`; forms of such tables show a **Files** section.

## The API

| Call | Does | Needs |
|---|---|---|
| `GET /api/v1/<module>/<table>/<id>/files` | The record's files (name, type, size, when, who), newest first | Reading the record |
| `POST /api/v1/<module>/<table>/<id>/files?name=<file name>` | Attaches the file sent as the body | Changing the record |
| `GET /api/v1/files/<id>` | The file; `?inline=true` shows a PDF or picture in the browser | Reading its record |
| `DELETE /api/v1/files/<id>` | Removes the file from its record | Changing its record |

A file of a record someone may not read answers "There's no such file", as if it weren't there.

## What's accepted

- **By content, not by name:** the first bytes decide. PDF, PNG, JPEG, GIF, WebP and HEIC
  pictures, Word, Excel and PowerPoint files (`.docx`, `.xlsx`, `.pptx`), and UTF-8 text (`.txt`,
  `.csv`, `.md`). Anything else is refused, web pages and SVG pictures included: a browser could
  run what's in them.
- **Size:** at most the setting **Largest file (MB)** (`sys.file_max_mb`, 20 by default, 1 to
  100). The upload stops as soon as it's larger.
- **Names** keep the file's own name without folders or characters file systems refuse, at most
  200 characters.

## Kept safely

- The content is kept in the database, **sealed with the installation's master key**: backups
  hold the files, and a backup alone doesn't show them. The record describing a file
  (`sys.file`) is audited like every record; the content isn't copied into the audit log.
- Removing a file marks its record; the content stays (nothing is deleted for real).
- Files are served so that they can't run: `X-Content-Type-Options: nosniff`, a content security
  policy that loads and runs nothing, and `Cache-Control: no-store`. Only PDFs and pictures are
  shown in the browser; everything else is downloaded.
- Administrators see which files there are (name, type, size, record) under `sys.file`, not their
  contents.
