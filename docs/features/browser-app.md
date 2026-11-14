# The browser app

The app people use in the browser. The web app serves it at the same address as the API, so it
works on any installation without setup. It is a client of the public API like any other: it can
do nothing a script with the same rights couldn't.

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

- **Home**: a greeting and the modules the person may open, with their items.
- **Lists** (`/list/<list view>`): the view's columns, sorted by a click on a header, a page at a
  time (records per page from the profile).
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
