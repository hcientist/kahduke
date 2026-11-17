# Transcript: adding the accessibility GitHub Action

A conversation with Claude Code (Opus 5.5) on 2026-11-17 that added
`.github/workflows/accessibility.yml` and the `a11y/` scanner. Tool calls are
summarized in *italics*.

---

## User

> create a github action for repo in pwd that runs accessibliity testing against all pages and files an issue in the github repo with the output if there are any a11y issues

## Claude

*Listed the repo's files, `package.json` and any existing workflows. Found a
Django app (`accounts`, `kwizzes`, `games`, `repos`) with no `.github/`
directory and the remote `hcientist/kahduke`.*

*Tried to read `pyproject.toml`, `.python-version`, `Dockerfile`,
`docker-compose.yml`, `.env.example`, all `urls.py` files and the README in one
shell command. The user rejected that command and interrupted.*

## User

> create a github action for repo in pwd that runs accessibliity testing against all pages and files an issue in the github repo with the output if there are any a11y issues

## Claude

*Read `pyproject.toml`, `kahduke_config/urls.py` and `README.md` with the file
reader instead. Then read the app URL files, the `seed_demo` command, the
settings, the game and kwiz views, the templates' forms and buttons, and
`static/js/game.js` to learn:*

- *how to run the app in CI (uv, Python 3.15, SQLite by default, Daphne for
  ASGI, `seed_demo` for a public demo kwiz);*
- *which pages need an ID or a signed-in user (kwiz detail and editor, the
  bank, repos, account pages);*
- *that the host and player game screens are drawn by `game.js` over a
  WebSocket, with buttons "Host a game", "Join", "Start the kwiz", "Show
  results now", "Next kwestion" and "Show final results", so a link crawler
  alone would miss them;*
- *that `DEBUG=False` uses no secure-only cookies, so a production-like
  server works over plain HTTP in CI.*

> Still exploring: the host and player screens are rendered by JavaScript, so
> I'm checking which buttons they draw before I write the crawler.

*Checked the latest versions on npm (`@axe-core/playwright` 4.14.0,
`playwright` 1.65.1).*

> Writing the scanner script now.

*Wrote `a11y/package.json` and `a11y/scan.mjs`. The scanner:*

- *crawls every same-origin link signed out, starting from the home, join,
  library, create, import and account pages;*
- *signs in, remixes the demo kwiz so owner-only pages appear, and crawls
  again;*
- *plays one whole game in two browsers, scanning host and player screens in
  the lobby, a question, after answering, results and the final screen;*
- *skips the admin, the API docs, static files, downloads, sign-out, and links
  out to GitHub or Canvas;*
- *checks WCAG 2.2 A and AA rules with axe-core and writes `a11y-report.md` and
  `a11y-results.json`.*

> Now the workflow.

*Wrote `.github/workflows/accessibility.yml`. It sets up the app with uv,
migrates, seeds the demo kwiz, creates a test account, runs `collectstatic`,
starts Daphne with `DEBUG=False`, installs the scanner and Chromium, runs the
scan, uploads the report, and files or updates one open issue labelled
`accessibility` (closing it on a clean run). Then removed an unneeded
`pull_request` condition, made `uv sync` use `--frozen`, and added the scanner's
output files and `node_modules` to `.gitignore`.*

> Now testing it end to end locally, against a throwaway database in the
> scratchpad so the real `db.sqlite3` isn't touched.

*Started the app on port 8765 with a scratch SQLite database, installed the
scanner, and ran it. All 44 pages and game screens were scanned, and the
whole game flow worked.*

> The full flow works: 44 pages and game screens scanned. Checking the report
> and removing duplicate `?next=` variants of the login page:

*The report showed the login page three times with different `?next=` values.
Changed the crawler to ignore `next` when deciding whether a page is new, and
re-ran: 2 failing elements, on `/accounts/login/` and
`/accounts/password/change/`. Stopped the server and removed the generated
`staticfiles/`.*

> I'll commit the npm lockfile and switch the workflow to `npm ci` so CI
> installs exactly what I tested:

*Changed `npm install` to `npm ci` in the workflow.*

I've added a GitHub Action that scans every page for accessibility problems and files an issue with the results when it finds any. I ran the scan locally and it works; the issue-filing step can't run outside GitHub, so it won't be tested until the workflow runs there. Nothing is committed yet.

**What I added**
- **`.github/workflows/accessibility.yml`** runs on every push to `main`, weekly on Mondays, and on demand from the Actions tab. It starts the app the way production does, on a throwaway database with the demo kwiz and a test account. Then it runs the scanner and keeps the report as a downloadable file on the run.
- **`a11y/scan.mjs`** checks each page against WCAG 2.2 A and AA rules, using axe-core (a standard accessibility checker) in a headless Chrome. It covers:
  - **Signed out:** every page reachable by following links on the site.
  - **Signed in:** the same crawl after logging in. It first remixes the demo kwiz so the editor and owner-only pages show up.
  - **A whole game:** one browser hosts and another joins, and it plays to the end. It checks the host and player screens in the lobby, during a question, after answering, on results, and on the final screen. These screens have no links to follow, so a plain crawl would miss them.
  - **Skipped:** Django admin, the API docs, downloads, sign-out, and links that go out to GitHub or Canvas.
- **`a11y/package.json`** and **`package-lock.json`** pin the scanner's dependencies. I also added the scanner's output files and `node_modules` to `.gitignore`.

**How the issue works:** there is only ever one open issue, labelled `accessibility`, so you won't get a new one on every push. Each run that finds problems rewrites that issue with the latest report and adds a comment. The first clean run closes it. The report groups problems by rule, worst first, and lists each failing page and element with a "how to fix" link.

**Local run:** it scanned 44 pages and game screens and found one real problem. The "Forgot your password?" link on `/accounts/login/` and `/accounts/password/change/` is too small a touch target: it's 17px tall, and WCAG 2.2 asks for at least 24px. So the first run on GitHub will open an issue for it.

Your repo settings don't need any changes: the workflow uses GitHub's built-in token and creates the `accessibility` label itself.

---

## User

> why does the a11y action need to run on a schedule if it's already running on push?

## Claude

It doesn't really need to. I added the schedule out of habit, and in this setup it adds almost nothing.

A scheduled scan is useful when something can change between pushes: a live site whose content changes, unpinned tools that pick up new rules, or data that grows. None of that applies here:

- **The app** only changes when someone pushes.
- **The scanner** is pinned in `a11y/package-lock.json` (axe-core rules, Playwright, Chromium), and the Python packages are pinned in `uv.lock`.
- **The data** comes from `seed_demo` on a fresh database every run.

So a Monday run checks the same code with the same tools and gets the same result as the last push. Its only effect is to reopen the issue if someone closed it without fixing anything, and you probably don't want that.

I'd drop the `schedule:` trigger and keep `push` to `main` plus `workflow_dispatch`, so you can still run it by hand. Want me to make that change?

---

## User

> yes, drop the schedule

## Claude

*Removed the `schedule:` trigger from the workflow.*

I removed the `schedule:` trigger from `.github/workflows/accessibility.yml`. The scan now runs on every push to `main`, and you can still run it by hand from the Actions tab. Nothing is committed yet.

---

## User

> write this transcript to a markdown file at docs/a11y-action.md

## Claude

*Wrote this file.*
