# The browser app

The app people use in the browser. The web app serves it at the same address as the API, so it
works on any installation without setup. It is a client of the public API like any other: it can
do nothing a script with the same rights couldn't.

## Setting up

A new installation opens with the web installer (`setup.md`): setup code, company, first
administrator, modules, safety; then the administrator sets up their second factor.

## Signing in

- Email and password, then the second factor: a code from an authenticator app, a recovery
  code, or a passkey. Sign-in providers, if any are set up, are offered as buttons.
- A person without a second factor sets one up right after the password: an authenticator app
  (with a QR code and the key as text) or a passkey. Recovery codes follow, to copy or download.
- An invitation or reset link (`/sign-in/invitation#…`) asks for a new password first.
- Whoever opens a page while signed out signs in and comes back to that page. Only pages of the
  app are accepted as the way back.

## The frame

- **Top bar**: the navigator button, the search, the person (to their profile) and signing out.
  An installation that isn't production shows its stage (QA, Test, Dev).
- **Navigator**: categories, modules, sections and items (`screens.md`), only what the person
  may open. A filter field matches labels, modules and keywords. Favorites: a star on each
  item, folders one level deep, reordered and moved with each favorite's menu. On screens
  narrower than 960 pixels the navigator is a drawer that closes after each choice.
- **Search**: on wide screens a bar with "Search in" (everywhere or one list), results as you
  type grouped per list plus matching navigator items, and a page with everything (`/search`).
  `Ctrl` or `⌘` with `K` goes to it.

## Pages

- **Home** and each module's dashboard (`/dashboard/<module>`): the person's widgets in a grid
  (cards of 1, 2 or 2×2 cells; one on small screens). **Customize** adds widgets from a picker
  (search, kinds, modules; new ones marked), removes them (not the required ones), reorders them
  (by dragging, or with ‹ › from the keyboard), sizes them; **Reset to default**.
  - Figures link to their list; a target shows how much is reached; a trend shows 12 months.
  - Charts: bars, a donut with its legend and shares, columns over time, a line with a
    crosshair; dates in 3 months, a year or all; clicking a bar or slice opens the matching
    list; every chart can show as a table.
- **Lists** (`/list/<list view>`), the same for every table:
  - **The address holds the list**: filters in the API's own form (`filter[kind][in]=service`),
    the sort, the search and the saved view, so a list can be bookmarked or sent.
  - **Filters**: "Add filter" builds one condition (a field, what to check, a value; dates also
    as "today", "30 days ago", "the start of this month" …); each condition shows as a chip in
    words, and its × removes it. Conditions all apply.
  - **Columns and sort**: a click on a header sorts; "Columns" chooses and orders them (by
    dragging, or with arrow buttons). Both are remembered per person.
  - **Saved views**: the columns, sort, filters and search under a name; one may open the list
    by default. "Reset to default" forgets what was remembered.
  - **Right-click** a value: show matching or filter it out, on or after / on or before (dates),
    this or more / less (numbers), where empty; open a referenced record; copy the value or a
    link. Right-click a header: sort, filter on it, hide it. With `Ctrl`, `⌘` or `Shift` the
    browser's own menu opens instead.
  - **Choosing records** (for those who may change them): "Update selected" sets one field on
    each, checked and recorded record by record, and says which weren't changed.
  - **Deleting** asks first and can be undone from the message that follows.
  - Choices that stand out (`tables.md`, tones) show in colour; a value the person may not see
    shows as "(no access)".
  - Below 720 pixels the list shows cards: the first column as the title, the second as the
    main line, the rest below.
- **Forms** (`/form/<table>/<id>`, `/form/<table>/new`), the same for every table:
  - The form view's sections, with an editor for each field type, or the value as text (and a
    link where it leads) when the person may not change it. Below 600 pixels, one column.
  - **Save** sends only what changed, with the record's version. If someone saved in between,
    their changes are taken, and where both changed a field the person chooses whose value to
    keep, field by field.
  - **Unsaved changes** stay in the browser tab: leaving by accident and coming back offers them
    again (secrets excepted). Closing the tab with changes asks first.
  - **Required fields** and the API's refusals show at their fields.
  - **Delete** asks first and can be undone; a deleted record says so and can be restored.
  - **Related**: a tab per kind of record pointing here, with its count; empty ones wait under
    "More". "New" in a tab makes a record that already points back.
  - **Actions**: the record's buttons, and more under ⋮, shown only when the server offers
    them for this record; they may ask first or ask for something. Lists offer them on chosen
    records and in a record's right-click menu.
  - **Form rules** hide fields, require them or lock them as the values change.
  - Who made and last changed the record, and when.
- **Profile**: light, dark or like the system; the colour theme; the language (English, German,
  or like the browser); records per page. Each is saved as soon as it changes.
- A page that doesn't exist says so; a list the person may not see says they have no access.

## Looks and languages

- Themes set the colours, corners, card style and font (`screens.md`); the default is Violet.
- English and German. Dates and numbers follow the language.
- Phone to desktop without scrolling sideways; WCAG 2.2 AA (checked in the browser tests); a
  link to skip to the content; less motion for those who ask their system for it.

## Protection

The app's page has a strict Content-Security-Policy: scripts, styles, fonts and calls only from
the installation itself, images also as data, no frames. Cookies are HttpOnly; every change
sends the CSRF token and comes from the installation's own address.
