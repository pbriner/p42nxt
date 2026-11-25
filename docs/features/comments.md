# Comments and work notes

A record can have a conversation: questions about an invoice, notes on an order, what the tenant
said. **Comments** are for everyone who may read the record; **work notes** are for the team
working on it, everyone who may change it. Both stay with the record, in its journal with its
changes.

## Allowing comments

```python
Table("order", "Order", {...}, comments=True)
Table("task", "Task", {...}, comments=True, work_notes=True)
```

The table's description in the API says `comments` and `work_notes`.

## The journal

Every form shows a **Journal**: what was said and what changed, in one timeline, newest first.

- Comments, work notes (marked as such) and the record's changes from the audit log: who changed
  which field from what to what. Only the fields the person may read.
- **Show**: everything, only notes and comments, or only changes.
- Writing: a work note or a comment, chosen above the box when both are possible. Work notes
  come first where the person may add them, so what the team says stays with the team unless
  someone chooses otherwise.
- A deleted record shows only its changes.

## The API

| Call | Does | Needs |
|---|---|---|
| `GET /api/v1/<module>/<table>/<id>/comments` | The comments, and the work notes if one may change the record (text, kind, when, by whom, whether they're one's own), newest first | Reading the record |
| `POST /api/v1/<module>/<table>/<id>/comments` | Adds one: `{"text": "…", "kind": "comment"}` (1 to 5000 characters, trimmed); `"kind": "work_note"` for a work note | Reading the record; changing it for a work note |
| `DELETE /api/v1/comments/<id>` | Removes one | Being its author |

Whoever may read a record may comment on it: a reader can ask about an invoice they can't change.
Work notes are read and added only by those who may change it.
Comments of a record someone may not read don't exist for them ("There's no such comment").

## Kept as said

- Comments are plain text: the app shows them as written, never as HTML or Markdown, so nothing
  in a comment runs or changes the page.
- Removing a comment marks it (`sys.comment`, audited like every record): the record's history
  and the audit log keep it.
- Each comment is published as the event `sys.comment_added` about its record, each work note as
  `sys.work_note_added` (`events.md`), so other modules can react (a task's watchers, for
  example). A work note is never published as a comment: whatever passes comments on (to people
  outside, say) never sees one.
- Someone who may read a record but not change it doesn't learn its work notes exist: they're
  left out of the list, and removing one says there's no such comment.
- Modules add and read both with `Client.comment()` and `Client.comments()` (`sdk.md`).
