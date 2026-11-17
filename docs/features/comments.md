# Comments

A record can have a conversation: questions about an invoice, notes on an order, what the tenant
said. Comments stay with the record they're about, next to its history.

## Allowing comments

```python
Table("order", "Order", {...}, comments=True)
```

The table's description in the API says `comments`; forms of such tables show a **Comments**
section above the history.

## The API

| Call | Does | Needs |
|---|---|---|
| `GET /api/v1/<module>/<table>/<id>/comments` | The comments (text, when, by whom, whether they're one's own), newest first | Reading the record |
| `POST /api/v1/<module>/<table>/<id>/comments` | Adds one: `{"text": "…"}` (1 to 5000 characters, trimmed) | Reading the record |
| `DELETE /api/v1/comments/<id>` | Removes one | Being its author |

Whoever may read a record may comment on it: a reader can ask about an invoice they can't change.
Comments of a record someone may not read don't exist for them ("There's no such comment").

## Kept as said

- Comments are plain text: the app shows them as written, never as HTML or Markdown, so nothing
  in a comment runs or changes the page.
- Removing a comment marks it (`sys.comment`, audited like every record): the record's history
  and the audit log keep it.
- Each comment is published as the event `sys.comment_added` about its record (`events.md`), so
  other modules can react (a task's watchers, for example).
