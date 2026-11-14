# Changelog

## 0.6.0

- Two widgets with their own source on the dashboard: how many languages people greet in, and
  the average words per greeting in each language (module routes
  `GET /api/v1/hello/widget/languages` and `/widget/words`).
- Needs the core 0.18 or later.

## 0.5.0

- A nightly job unpins greetings pinned more than 30 days ago; it acts with the new
  "Greetings keeper" role, which only hello's jobs get.
- Needs the core 0.13 or later.

## 0.4.0

- A dashboard: pinned greetings, greetings by language and per month, and a note about
  greetings; on home, the number of greetings. Its navigator has a Dashboard item.
- Needs the core 0.11 or later.

## 0.3.0

- Greetings can be pinned and unpinned (buttons, and on several at once), and signed (asks for
  the signature).
- A greeting is for everyone or for one team; a team greeting needs its team, which shows only
  then. The author can't be changed.
- Needs the core 0.10 or later.

## 0.2.0

- In the navigator under Examples: the greetings, and leaving one. The list shows text,
  language, author and when.
- Needs the core 0.6 or later.

## 0.1.0

- Greetings: anyone with the Greeter role reads them and leaves their own; authors change or
  delete only theirs.
