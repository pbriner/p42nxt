# Screens

What the app shows besides records is records too: the navigation, the lists, the themes, and
each person's own choices. Modules declare theirs; the app reads them through the same API as
any other client.

## Navigation

The navigator has three levels: a module, its sections, their items. Modules are grouped into
categories (the core's category, Platform, comes last).

A module declares its navigation in its manifest:

```python
navigation=(
    Section("Greetings", (
        Item("greetings", "Greetings", "hello.greeting", icon="chat"),
        Item("greet", "Leave a greeting", "hello.greeting", kind="create", icon="add"),
    )),
),
category="Examples", icon="waving_hand",
```

| Part of an item | Meaning |
|---|---|
| `name` | Unique in the module; the record is named `<module>.<name>` |
| `label` | Text, or a text per language (`{"en": …, "de": …}`) |
| `table` | What it opens |
| `kind` | `list` (the default), `create` (a new record's form) or `page` (a page of the app, `path`) |
| `view`, `filter` | A list view other than the table's default, and a filter on top of it |
| `icon`, `keywords` | A Material Symbol's name; words the navigator's filter also matches |

Installing a module writes its items as records of `sys.nav_item`; items it no longer declares
are retired. An item is checked first: its table must exist, a filter must be valid on it, a page
must be a path in the app.

`GET /api/v1/navigation` returns what the caller may open: an item shows to whoever may read its
table (for `create`: make records in it). Modules without such items, and switched-off modules,
don't show. Labels come in the caller's language.

## List views

A list view says what a list of a table shows: its columns, sort, filter, the further columns
people may add, and the fields the search looks in. Its name is the table's (`hello.greeting`).

```python
views=(ListView("hello.greeting", ("text", "language", "author", "created")),),
```

Every table gets a list view: a table whose module declares none gets one made from its fields
(the display field first, then up to four more). Unless a view says otherwise, the search looks
in its text columns. The core's made views stay out of the search; the core's people, groups,
roles and modules are in it.

## Form views

A form view says how a record of a table is shown and edited: sections with their fields and
columns (1 to 4), and the related lists to show first. Its name is the table's.

```python
forms=(FormView("task.task", (
    FormSection("Task", ("title", "state", "assignee")),
    FormSection("Details", ("description",), columns=1),
), related=(RelatedList("task.note", "task"),)),),
```

Every table gets one: a table whose module declares none gets one section with all its fields
in two columns. Related lists are the declared ones, then (unless `related_auto=False`) every
other table pointing to the record through a field the person may read.

## Each person's own

These are personal tables: only their owner sees their records, administrators included. They
use the normal record routes (`/api/v1/sys/<table>`), and the owner is set to whoever makes
the record.

| Table | What |
|---|---|
| `sys.preference` | One per person: mode (like the system, light or dark), theme, records per page (10, 20, 30, 40, 50, 100, 500, or 5000 for all), language |
| `sys.favorite` | Navigation items marked as favorites, and folders of them, ordered by `position`. Folders are one level deep; deleting a folder moves its favorites to the top level. At most 200 |
| `sys.saved_view` | A list's columns, sort, filter and search under a name. One of a person's saved views of a list may open by default; marking another moves the mark. At most 100 per list |
| `sys.view_state` | The columns and sort a person last used on a list, one per list |

Columns, sort and filters are checked against the list's table when they're saved.

## Themes

A theme sets colours (each with a light and a dark value), the corner radius (0 to 24 pixels),
the card style (outlined or raised) and the font (Roboto or the system's). Colours it doesn't set
keep the app's built-in ones. The colours a theme may set are the Material colour roles
(`primary`, `surface-container-high` …), the parts of the app's frame (`page-background`,
`toolbar`, `nav-background`, `nav-background-end`, `on-nav`, `nav-accent`, `nav-active` …) and
the chart colours (`chart-1` to `chart-8`, `chart-other`), each as `#rrggbb`.

The core brings two: **Violet**, the default, and **Blue**. Administrators make their own
(`/api/v1/sys/theme`); a module's theme isn't changed in place, it's copied.

`GET /api/v1/theme?name=…` returns a theme's colours and look: the one named, else the
installation's default (the `sys.theme` setting), else Violet. It needs no sign-in, so the
sign-in page looks like the app.

## Search

`GET /api/v1/search?q=…` searches every list the caller may read at once (or one, with
`scope=<list view>`), in each list's search fields. It returns a group per list with hits: the
list, its module, the total, and up to `limit` records (1 to 20, default 5), each as a title (its
display value and first column) and a subtitle (the next three columns). Records are read with
the caller's access, as in the list itself: what they may not see is neither found nor counted.
The query is 2 to 100 characters.

`GET /api/v1/search/scopes` lists the lists the caller may search in.

## Settings

| Setting | Default | What |
|---|---|---|
| `sys.theme` | `violet` | The theme everyone sees unless they choose another. Public |
| `sys.stage` | `production` | What the installation is for: `production`, `qa`, `test` or `development`. Public |
