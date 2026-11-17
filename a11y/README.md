# Accessibility scan

`scan.mjs` checks every page of KahDuke against WCAG 2.2 A and AA with
axe-core. The [Accessibility workflow](../.github/workflows/accessibility.yml)
runs it on every pull request and every push to `main`. On `main`, the results
go into one open GitHub issue labelled `accessibility`.

## What it covers

- **Signed out:** a crawl that follows every same-origin link, starting from
  the start paths in `scan.mjs`. It also checks the 404 page, and the private
  edit link that a kwiz made without signing in shows.
- **Signed in:** the same crawl as a test account, after it remixes the demo
  kwiz so the owner-only pages show up.
- **A whole game:** one host and two players, scanning the lobby, a kwestion,
  "answer locked in", the results and the final screen.

## When the workflow fails

Violations alone don't fail the workflow; they go in the issue. It fails when
the scan is **incomplete**, which usually means the app changed. The "Scan
problems" section of the run summary says what went wrong:

| Problem | What to do |
| --- | --- |
| `route not scanned: …` | You added a page the scan doesn't reach. Link to it from a page that is scanned, add it to a crawl's start paths, or add it to [`EXCLUDE`](./exclude.json) with a reason. |
| `EXCLUDE entry "…" matches no route` | A route was renamed or removed. Update or delete the entry. |
| `game step "…"` or `signed-in setup` | A control the flow uses has gone. Check that the `data-testid` attributes are still there (see below). |
| `HTTP 4xx/5xx` | A linked page is broken. If it returns that status on purpose, add it to `ERROR_STATUS`. |
| `stopped at A11Y_MAX_PAGES` | The site outgrew the limit. Raise `A11Y_MAX_PAGES`. |

## Keep these in sync

- **`data-testid` attributes.** The game flow and setup find controls by these
  attributes, not by their wording or styling. Keep them if you rewrite the
  markup:
  - `kwizzes/templates/kwizzes/detail.html`: `host-game`, `remix`, `edit-link`
  - `games/templates/games/join.html`: `join`
  - `static/js/game.js`: `start-game`, `close-kwestion`, `next-kwestion`
    (with `data-last` on the last kwestion), `answer-choice`, `player-lobby`,
    `answer-locked`, `player-result`, `final-results`
- **Form field names.** It fills in `nickname`, and allauth's `login` and
  `password` fields.
- **[`exclude.json`](./exclude.json).** Add any new route that changes data on GET,
  signs out, leaves the site, or isn't an HTML page, with `skip: true`. The
  crawler is signed in as a superuser and follows every link it finds.
- **The demo kwiz.** `app_info.py` finds it by the title that
  `kwizzes/management/commands/seed_demo.py` gives it, so the scan needs
  `seed_demo` to keep making a playable public kwiz.

## Running it locally

Start the app with a database that has been migrated and seeded, plus an
account to sign in with. Then:

```sh
uv run python a11y/app_info.py > a11y/app.json   # against the same database
cd a11y
npm ci && npx playwright install chromium
BASE_URL=http://127.0.0.1:8000 A11Y_USER=… A11Y_PASSWORD=… node scan.mjs
```

Run the server with `DEBUG=False` and `collectstatic`, as CI does, and restart
it after you change templates, because templates are cached.
