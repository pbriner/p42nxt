# Frontend

The browser app: Angular with Material 3. It uses only the public API, the same one any script
uses, and knows no table of its own: lists, the navigator and the search are built from records
read through the API (`docs/architecture.md`).

## Layout

| Folder           | What                                                                                                                                                      |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/app/core/`  | Talking to the API (`api.ts`: CSRF header, problem details, signed out → sign in), the session, preferences, themes, navigation, favorites, search, texts |
| `src/app/i18n/`  | The texts: `en.ts` is the reference, every other language translates all of it (the build fails otherwise)                                                |
| `src/app/shell/` | The frame: top bar, navigator, search bar                                                                                                                 |
| `src/app/pages/` | One file per page                                                                                                                                         |
| `e2e/`           | The Playwright tests and the installation they run against                                                                                                |

## Working on it

```bash
npm ci
npm start          # ng serve on :4200, the API proxied to localhost:8000 (p42cloud-web)
npm run build      # into dist/browser, which the web app serves
npm run format     # Prettier
npm run e2e        # the browser tests (below)
```

## Browser tests

`npm run e2e` starts a real installation (`e2e/installation.py`): a fresh database in the
development PostgreSQL with the core and the modules, some people with passwords and
authenticator keys, and the web app serving `dist/browser` on port 8898 (`P42_E2E_PORT` to
change it). The database is dropped when the run ends; one a killed run left behind is dropped
by the next. Build first.

Every test runs at five sizes: desktop light and dark (1440×900), laptop (1280×800), tablet with
touch (820×1180) and phone (Pixel 7, dark). Every page is checked for WCAG 2.2 AA with axe, and
for scrolling sideways.

To try the app by hand, start the same installation on port 8899 and open
`http://localhost:8899`:

```bash
uv run --directory ../backend python ../frontend/e2e/installation.py --port 8899
```

Its people and their authenticator keys are in `e2e/.state-8899.json`.

## Rules

- Colours only through tokens (`--mat-sys-*`, `--app-*`, `--chart-*`); themes set them.
- Every text through `I18n.t`, in English and German.
- No inline scripts or `eval`: the page's Content-Security-Policy allows scripts only from the
  app's own files.
