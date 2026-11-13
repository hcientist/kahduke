# KahDuke

A Kahoot-style live kwiz game for CS 347. Write a kwiz, put the host screen on
the projector, and players answer from their phones with a six-digit code.
Nobody needs an account; signing up later claims everything you made or played
in that browser.

Built with Python 3.15, Django 6.1, django-allauth (accounts), Django Channels
(live games over WebSockets), django-ninja (the JSON API the editor uses),
django-environ (configuration) and WhiteNoise (static files). The data model
is in [models.md](models.md).

## Run it locally

You need [uv](https://docs.astral.sh/uv/). It installs Python 3.15 for you.

```sh
uv sync                                   # create .venv and install everything
uv run python manage.py migrate
uv run python manage.py seed_demo         # optional: a public demo kwiz
uv run python manage.py createsuperuser   # optional: for /admin/
uv run python manage.py runserver
```

Then open http://127.0.0.1:8000. To play locally, host a kwiz in one browser
window and join from a private window (or a phone on the same network, with
`runserver 0.0.0.0:8000` and your machine's IP in `ALLOWED_HOSTS`).

No `.env` is needed. Without one you get SQLite (`db.sqlite3`) and an
in-memory channel layer, which is fine for one `runserver` process. To use
Redis like the server does, start one and set `REDIS_URL` in `.env` (see
[.env.example](.env.example)):

```sh
docker run --rm -p 6379:6379 redis:7
```

### Tests and linting

```sh
uv run python manage.py test
uv run ruff check . && uv run ruff format --check .
```

The tests include a whole game played over WebSockets (`games/tests.py`).

## Run it in containers

`docker-compose.yml` runs the production setup on your laptop: Postgres,
Redis, and Daphne with `DEBUG=False`.

```sh
docker compose up --build    # http://localhost:8000
```

## Deploy to the course VM

This follows the [CS 347 Google Cloud guide](https://cs-347-f26.github.io/google-cloud/).
The shared `/srv/apps` setup already has a `kahduke` service, a Caddy block for
`KAHDUKE_HOST`, and a `kahduke` Postgres database.

1. On the VM, clone this repo as `/srv/apps/kahduke`.
2. Make sure `/srv/apps/.env` sets `KAHDUKE_HOST`, `ALLOWED_HOSTS` and
   `CSRF_TRUSTED_ORIGINS`.
3. Give the `kahduke` service Redis. The course's stanza only sets
   `DATABASE_URL`. Without `REDIS_URL`, KahDuke falls back to the in-memory
   channel layer, which works with Daphne's single process but not with more
   than one. In `/srv/apps/docker-compose.yml`:

   ```yaml
   kahduke:
     <<: *django
     build: ./kahduke
     environment:
       DATABASE_URL: postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/kahduke
       REDIS_URL: redis://redis:6379
     depends_on:
       postgres:
         condition: service_healthy
       redis:
         condition: service_started
   ```

4. Build and start it. Migrations run when the container starts.

   ```sh
   docker compose up -d --build kahduke
   ```

To ship an update: `git -C kahduke pull && docker compose up -d --build kahduke`.

The `Dockerfile` is the course's `Dockerfile.example` with these changes:
- the Python 3.15 base image;
- the Daphne `CMD`, because Channels needs ASGI;
- `AUTOBAHN_USE_NVX=0`, because Daphne's `autobahn` dependency has no prebuilt
  Linux wheel for 3.15 yet and has to be built from source;
- no `REDIS_URL` placeholder, because it's optional in `settings.py`.

## How it fits together

| App | What it owns |
| --- | --- |
| `accounts` | The custom `User`, and the sign-in signal that claims anonymous work. |
| `kwizzes` | Kwizzes, the kwestion bank, answer choices, the editor, the library, remixing. |
| `games` | Game sessions, players, responses, scoring, and the WebSocket consumer. |

- **Anonymous ownership.** Making a kwiz, hosting a game or joining one stores
  a secret token in your Django session (`kwizzes/access.py`). On sign-in or
  sign-up, `accounts/signals.py` hands every matching row to the new account.
  Anonymous creators also get a private edit link, so they can edit from
  another browser.
- **Editor.** `static/js/editor.js` loads a kwiz from `GET /api/kwizzes/{id}`
  and saves the whole thing with one `PUT`. The schemas are in
  `kwizzes/schemas.py` and the save logic is in `kwizzes/editing.py`. Writes
  require a CSRF token even for anonymous users. Interactive API docs are at
  `/api/docs`.
- **Markdown.** `kwizzes/markdown.py` parses a kwiz written in Markdown into
  the same `KwizIn` payload the editor sends, and renders a kwiz back. The
  format is documented at the top of that file and on the import page
  (`/kwizzes/import/`). Every kwiz has a "Download .md" link, and its editors
  get "Edit as Markdown", which re-imports in place: kwestions and choices are
  matched by position, so earlier games keep their responses.
- **Live games.** `games/consumers.py` gives each screen one socket. The host
  sends `start`, `close`, `next` and `end`; players send `answer`. After every
  change the server tells each screen to refresh, and each one gets its own
  snapshot from `games/state.py`. Players never receive which choices are
  correct until the results show. Reconnecting just loads the current
  snapshot again.
- **Scoring.** A choice is worth `correctness × kwestion points`, or its own
  points if it has them. An instant answer earns the full amount and an
  answer at the buzzer earns half (`games/models.py:score`).

The palette is the JMU CS swatch set: JMU Purple `#450084`, CS Teal `#009896`,
CS Ice Blue `#8EE4D7`, JMU Light Gold `#F4EFE1` and Slate Gray `#333333`.
