# Accessibility scan

`scan.mjs` checks every page of KahDuke against WCAG 2.2 A and AA with
axe-core. The [Accessibility workflow](../.github/workflows/accessibility.yml)
runs it on every pull request and every push to `main`. On `main`, the results
go into one open GitHub issue labelled `accessibility`.

## How it runs on GitHub

You don't have to run the scan yourself: GitHub runs it for you. Here's how
that works if you haven't used GitHub Actions before.

**GitHub Actions** is GitHub's built-in automation. A repo describes jobs in
YAML files in `.github/workflows/`, and GitHub runs any file it finds there on
a fresh Linux machine whenever that file's triggers happen. This scan's file
is [`.github/workflows/accessibility.yml`](../.github/workflows/accessibility.yml).
Nobody has to start or configure anything: the file being in the repo is
enough.

**When it runs.** The `on:` section at the top of the YAML lists the triggers:

- `pull_request`: every time you open a pull request or push to its branch.
- `push` to `main`: every time a change lands on `main`, including a merged
  pull request.
- `workflow_dispatch`: by hand, from the repo's **Actions** tab, using
  **Run workflow**.

**What it does.** The `steps:` in the YAML run one after another, much like
typing the commands yourself:

1. Check out the repo, and install Python (with `uv`) and the app.
2. Set up a brand-new database: `migrate`, `seed_demo` for the demo kwiz, and
   `createsuperuser` for a test account to sign in with.
3. Run `app_info.py`, which writes the list of the app's routes and the demo
   kwiz's ID to `a11y/app.json`.
4. Start the app with Daphne, the way production does.
5. Install Node, Playwright and Chromium, and run `node scan.mjs`.
6. Save the report, and on `main`, file or update the GitHub issue.

Each run starts from nothing, so it never sees your laptop's database or
settings. If the scan passes on your machine but fails here, that difference
is usually why.

**Where to see the results.**

- **On a pull request:** the "Accessibility / scan" check at the bottom of
  the conversation tab. Click **Details**, then **Summary**, to read the report.
  A red ✗ means the scan was incomplete (see
  [When the workflow fails](#when-the-workflow-fails)). Violations alone leave
  it green, so read the report even when it passes.
- **In the Actions tab:** every run, with each step's output. Open a run and
  download the **a11y-report** artifact for the full report, the JSON results,
  and the server log.
- **In Issues:** after changes land on `main`, the open issue labelled
  `accessibility` lists everything still failing. It closes itself once a
  complete scan finds nothing.

**Keeping it up to date.** The scanner's versions are pinned in
`a11y/package.json` and `a11y/package-lock.json`, so every run uses the same
tools. Another file, `.github/dependabot.yml`, has GitHub open a pull request
each week when there are newer versions of those packages or of the actions
the workflow uses. A newer axe-core can add rules, so merging one of those
pull requests may add new violations to the issue.

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
