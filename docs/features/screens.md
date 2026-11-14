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

## UI actions

A UI action is a button or menu entry that does something with a record. It is a record of
`sys.ui_action`.

```python
UIAction("pin", "hello.greeting", "Pin",
         places=("form_button", "list_bulk", "list_row"), icon="push_pin",
         condition={"field": "pinned", "op": "eq", "value": False},
         set={"pinned": True}, message="Pinned.")
```

| Part | Meaning |
|---|---|
| `places` | `form_button`, `form_menu` (the form's ⋮), `list_bulk` (on chosen records), `list_row` (a record's right-click menu) |
| `operation` | What the person must be allowed to do with the record: `write` (default), `delete` or `read` |
| `condition` | When it's offered, in the filter language (`status = draft`, `assigned_to = @me`) |
| `confirm` | A question asked first |
| `inputs` | What it asks for (`ActionInput`: text, choices, yes/no, dates, numbers) |
| `second_factor` | The person enters a code again first |
| `set` | Fields it sets, without code; `"@input.<name>"` takes what was entered |
| `handler` | Or the module's code: `async def handler(run: ActionRun) -> ActionResult` |
| `message`, `go` | What it says when done; `stay` or go to the `list` |

Records carry `_actions`: the actions the person may run on each now (allowed and the condition
holds). Running one is `POST /api/v1/<module>/<table>/<id>/action/<name>` (with `If-Match`
optionally), or `POST /api/v1/<module>/<table>/action/<name>` with `ids` for several, each on
its own with a result per record. The server checks everything again; the work and its audit
entry happen together. Modules run actions with the SDK client's `action()`.

## Form rules

A form rule hides fields, makes them required or read-only while a condition holds. It is a
record of `sys.form_rule`.

```python
FormRule("team_needed", "hello.greeting",
         condition={"field": "audience", "op": "eq", "value": "team"},
         required=("team",))
```

The form applies rules while the person types. The server applies them again on every save,
whatever the browser did: a field a rule locks (on the record as it was) may not change, and a
field a rule requires (on the record as saved) may not be empty. Rules only hide or lock more;
what a person may read or change is still decided by their roles. Conditions use the table's
own fields with these operators: `eq`, `ne`, `in`, `not_in`, `empty`, `not_empty`, `contains`,
`not_contains`, `starts_with`, `ends_with`, `lt`, `lte`, `gt`, `gte`, `between`, and `@me`.

## Widgets and dashboards

Home and each module have a dashboard of widgets. A widget is a record of `sys.widget`; its data
comes from a list view, read through the API as the person, so each sees only what they may.

```python
Widget("by_language", "By language", "chart", list="hello.greeting",
       group="language", chart="donut", dashboard="hello")
```

| Part | Meaning |
|---|---|
| `kind` | `figure` (a number, with an optional `target` and a 12-month `trend` by a date field), `chart` (`bar`, `donut`, `column`, `line`, grouped by a field; dates per `period`), `list` (the first records), `text` |
| `list`, `filter` | The list view it reads, and a filter on top |
| `measure`, `field` | `count` (default), or `sum`, `avg`, `min`, `max` of a number field |
| `dashboard` | `home`, or the module's own |
| `icon`, `accent`, `filled` | An icon tile, its chart colour (1 to 8), the whole card in that colour |
| `sizes`, `size`, `refresh` | Allowed sizes (`s`, `wide`, `large`), the starting one; reload every so many seconds |
| `shown` | For everyone who may read it: `available` (in the picker), `default` (placed), `mandatory` (always there) |
| `source` | For what a list can't count or sum: a GET route of the module (below) |

Administrators set how a widget is shown per group with `sys.widget_audience`; the strongest
for a person counts. `GET /api/v1/dashboards/<name>` returns the widgets a person may see and
their arrangement: their own (`sys.dashboard_layout`, a personal record), else the defaults;
mandatory widgets are always in it, and widgets they haven't seen offered yet are `new`.

Totals come from `GET /api/v1/<module>/<table>/aggregate` (`api.md`).

### Source widgets

A figure or a chart can take its data from one of its module's GET routes (`module-routes.md`)
instead of counting a list: running totals, averages, profit over time.

```python
Widget("languages", "Languages", "figure", list="hello.greeting",
       source="/widget/languages", dashboard="hello")
```

The list still decides who may see the widget; the route reads through the client, so it counts
only what the caller may read. A figure's route answers
`{"value": 3, "unit": "languages", "detail": "Most greetings in German.", "tone": "good",
"link": "/list/hello.greeting"}` (`text` instead of `value` for a word, `tone` `good` or `bad`
for the detail, `link` a page of the app); a chart's
`{"subtitle": "…", "points": [{"key": "de", "label": "German", "value": 3.5}]}`. A chart of one
series is drawn in the widget's accent; a donut gives each slice its colour.

A source widget can be about one record the person picks:
`Widget(..., source="/widget/profit", param=("plan", "dca.plan"))`. The dashboard offers that
table's records (those the person may read) above the widgets, keeps the choice in the address
(`?plan=<id>`; the first record by default) and passes it to the route (`?plan=<id>`); with
nothing to choose, the card says so.

## Each person's own

These are personal tables: only their owner sees their records, administrators included. They
use the normal record routes (`/api/v1/sys/<table>`), and the owner is set to whoever makes
the record.

| Table | What |
|---|---|
| `sys.preference` | One per person: mode (like the system, light or dark), theme, records per page (10, 20, 30, 40, 50, 100, 500, or 5000 for all), language |
| `sys.favorite` | Navigation items marked as favorites, and folders of them, ordered by `position`. Folders are one level deep; deleting a folder moves its favorites to the top level. At most 200 |
| `sys.saved_view` | A list's columns, sort, filter and search under a name. One of a person's saved views of a list may open by default; marking another moves the mark. At most 100 per list |
| `sys.related_layout` | One per form: the related lists a person chose, in order, and the tab open last |
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
