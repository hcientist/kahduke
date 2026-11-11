# Kahduke Data Models

Entity-relationship sketch for the kahoot clone. A `?` in an attribute comment means the column is nullable.

```mermaid
erDiagram
    USER {
        uuid id PK
        string email UK
        string display_name
        string password_hash
        datetime created_at
    }

    KWIZ {
        uuid id PK
        uuid owner_user_id FK "? null = anonymous creator"
        string edit_token UK "secret link for anonymous creators"
        string title
        text description
        enum visibility "private | public"
        enum reveal_mode "full | counts | hidden"
        uuid remixed_from_kwiz_id FK "? self-ref, source of a remix"
        datetime created_at
        datetime updated_at
    }

    KWESTION {
        uuid id PK
        uuid owner_user_id FK "? the user's kwestion bank"
        uuid remixed_from_kwestion_id FK "? self-ref, copied from"
        text prompt
        string media_url "?"
        enum scoring_mode "binary | scale"
        int default_points
        int default_time_limit_sec
        datetime created_at
    }

    KWIZ_KWESTION {
        uuid id PK
        uuid kwiz_id FK
        uuid kwestion_id FK
        int position "order within kwiz, unique per kwiz"
        int points_override "? else KWESTION.default_points"
        int time_limit_override_sec "? else KWESTION.default_time_limit_sec"
        enum reveal_mode "blank = KWIZ.reveal_mode"
    }

    ANSWER_CHOICE {
        uuid id PK
        uuid kwestion_id FK
        int position "order within kwestion, unique per kwestion"
        text body
        string media_url "?"
        decimal correctness "0.0-1.0; binary uses 0 or 1"
        int points "? overrides correctness x kwestion points"
    }

    GAME_SESSION {
        uuid id PK
        uuid kwiz_id FK
        uuid host_user_id FK "? null = anonymous host"
        string host_token UK "authorizes host controls"
        string shortcode UK "join code shared with players"
        enum status "lobby | in_progress | finished"
        int current_position "? position of active kwestion"
        datetime kwestion_opened_at "? null while showing results"
        datetime created_at
        datetime started_at "?"
        datetime ended_at "?"
    }

    PLAYER {
        uuid id PK
        uuid game_session_id FK
        uuid user_id FK "? null = anonymous player"
        string nickname "unique per game session"
        string session_token UK "reconnects anonymous players"
        int total_score
        datetime joined_at
    }

    RESPONSE {
        uuid id PK
        uuid player_id FK
        uuid kwiz_kwestion_id FK
        uuid answer_choice_id FK "? null = no answer / timed out"
        int response_ms "time from kwestion open to answer"
        int points_awarded
        datetime answered_at
    }

    USER  |o--o{ KWIZ          : "creates"
    USER  |o--o{ KWESTION      : "banks"
    KWIZ  |o--o{ KWIZ          : "remixed into"
    KWESTION |o--o{ KWESTION   : "remixed into"

    KWIZ     ||--|{ KWIZ_KWESTION : "contains (ordered)"
    KWESTION ||--o{ KWIZ_KWESTION : "used in"
    KWESTION ||--|{ ANSWER_CHOICE : "offers (ordered)"

    KWIZ         ||--o{ GAME_SESSION : "hosted as"
    USER         |o--o{ GAME_SESSION : "hosts"
    GAME_SESSION ||--o{ PLAYER       : "joined by"
    USER         |o--o{ PLAYER       : "plays as"

    PLAYER        ||--o{ RESPONSE : "submits"
    KWIZ_KWESTION ||--o{ RESPONSE : "answered in"
    ANSWER_CHOICE |o--o{ RESPONSE : "selected in"
```

## Notes

- **Kwestion bank / reuse.** Kwestions are standalone rows owned by a user. `KWIZ_KWESTION` is the ordered join, so one kwestion can appear in many kwizzes. Points and time limits live on the kwestion as defaults, and each kwiz can override them per slot.
- **Ordering.** `KWIZ_KWESTION.position` is unique per `kwiz_id`, and `ANSWER_CHOICE.position` is unique per `kwestion_id`.
- **"1+" cardinality.** The `||--|{` edges mark the at-least-one rule. The database can't enforce it on its own, so the app checks it before a kwiz can be published or hosted.
- **Correctness.** `correctness` is a decimal in [0, 1]. In `binary` mode choices are 0 or 1. In `scale` mode partial credit is allowed (e.g. 0.5). By default a choice earns `correctness × kwestion points`, scaled by speed. A non-null `ANSWER_CHOICE.points` replaces that.
- **Reveal mode.** After a kwestion closes, `full` shows which choices were right and how many players picked each. `counts` shows only how many picked each, and `hidden` shows nothing. A kwiz sets the default and each slot can override it. Only `full` kwestions add to scores, because a change in score would show who got the answer right. The other two still record each player's choice, with `points_awarded = 0`.
- **Public & remix.** Anyone can host a kwiz whose `visibility = public`. Remixing deep-copies the kwiz and its kwestions and choices to the new owner, and sets the `remixed_from_*` pointers for attribution.
- **No account required.** Creators, hosts and players can all be anonymous. Each role has a nullable `user_id` and a secret token: `edit_token`, `host_token` or `session_token`. A user who signs up later can claim their rows by token.
- **Shortcode.** `GAME_SESSION.shortcode` only has to be unique among active sessions, so codes can be reused after a game finishes. It's enforced with a partial unique index on `status != finished`.
- **Game clock.** `kwestion_opened_at` is set when a kwestion opens and cleared when its results show. The server times each answer from it (`RESPONSE.response_ms`) and rejects answers that arrive after the time limit plus a short grace period.
- **Editing played kwizzes.** Removing a kwestion from a kwiz deletes its `KWIZ_KWESTION` slot, and that slot's responses with it. Snapshotting (below) would preserve them.
- **Possible extensions (not modeled):**
  - Multi-select answers would need a `RESPONSE_CHOICE` join table.
  - Snapshotting the kwiz at game start would protect a running game from mid-game edits.
  - Tags or folders could organize the kwestion bank.
