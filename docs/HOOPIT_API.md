# Hoopit Mobile API Reference (Unofficial)

Discovered from the Hoopit Android app (`io.hoopit.app.android.v2`) via static analysis and read-only runtime tracing.

## Base URLs

| Service | Base URL | Purpose |
| --- | --- | --- |
| Hoopit Core API | `https://api.hoopit.io` | Users, groups, events/occurrences, RSVPs, posts, comments, notifications, drive files, chat tokens |
| Stream Chat API | `https://chat.stream-io-api.com` | Direct, group, and guardian chats (GetStream Chat REST API, `api_key=y8afyfq3u8a5`) |
| Firebase Token Refresh | `https://securetoken.googleapis.com/v1/token` | Refresh Firebase ID tokens (`project: hoopit-30d5c`) |
| Firebase Email/Password | `https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword` | Sign in with email/password if enabled on the account |

## Authentication

Hoopit authenticates API requests using **Firebase Authentication** (`project: hoopit-30d5c`) ID tokens:

- **Header**: `Authorization: Bearer <firebase_id_token>`
- **Token Lifetime**: 3600 seconds (1 hour)
- **Refresh Flow**:
  - `POST https://securetoken.googleapis.com/v1/token?key=<FIREBASE_API_KEY>`
  - Form/JSON body: `{"grant_type": "refresh_token", "refresh_token": "<refresh_token>"}`
  - Response: `{"id_token": "...", "refresh_token": "...", "expires_in": "3600", "user_id": "..."}`
  - Works regardless of how the user originally signed in (phone OTP, Vipps, NIF, or email/password).
- **Email/Password Flow** (optional):
  - `POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=<FIREBASE_API_KEY>`
  - JSON body: `{"email": "...", "password": "...", "returnSecureToken": true}`
  - Response: `{"idToken": "...", "refreshToken": "...", "expiresIn": "3600"}`

### Standard Request Headers

```http
Authorization: Bearer <firebase_id_token>
Accept: application/problem+json; version=<N>;
Accept-Language: no
X-Client-Name: app
App-Platform: android
App-Version: 2.172.3-15567
```

Hoopit uses content-negotiated API versioning via the `Accept` header (`version=<N>`). Responses include `Content-Version`, `View-Id`, and (when outdated) `Deprecated-Version`.

---

## Core API Endpoints (`https://api.hoopit.io`)

### 1. Current User & Family

- **`GET /app/users/current/`** (`version=6`)
  - Returns current user profile (`id`, `uuid`, `first_name`, `last_name`, `email`, `auth_phone_number`) plus `children[]` (`id`, `uuid`, `first_name`, `last_name`, `full_name`, `age`, `supervised_by_parents`) and `guardians[]`.
- **`GET /app/users/current/tokens/`** (`version=1`)
  - Returns `id`, `uuid` (used as Stream Chat `user_id`), and `stream_chat_token` (HS256 JWT for Stream Chat).

### 2. Groups

- **`GET /app/users/current/groups/`** (`version=2`)
  - Returns array of groups:
    - `id` (int), `name` (str), `organization_name` (str), `division_name` (str), `parent_group_name` (str | null)
    - `members[]`: `{id, first_name, is_admin, is_player, is_last_group, profile_picture}`
    - `available` (bool), `is_nif_connected` (bool)
- **`GET /app/organizations/my/groups/hierarchy/?scope=your_groups`** (`version=1`)
  - Returns nested organization/group hierarchy (`id`, `name`, `group_type`, `parent_group_id`, `organization_name`, `children[]`).

### 3. Events & Occurrences

Hoopit models events as `(event_id, occurrence_id)` pairs (recurring events share `event_id` and have distinct Unix-timestamp `occurrence_id` values). In `hoopit-mcp`, event identifiers are exposed as `"<event_id>:<occurrence_id>"`.

- **`GET /app/users/current/events/?page={page}&pagination_count=false`** (`version=3`)
  - Paginated (`has_next`, `results[]`). Each item:
    - `id` (int — occurrence ID), `event_id` (int)
    - `title` (str), `datetime` (ISO 8601), `end` (ISO 8601), `week_number` (int)
    - `location` (str), `event_type` (str), `group` (`{id, name, color_scheme}`)
    - `spots_left` (int), `is_admin` (bool), `is_muted` (bool)
    - `accepted_participant_count`, `declined_participant_count`, `not_responded_participant_count`
    - `accepted_player_count`, `declined_player_count`, `not_responded_player_count`
    - `multi_answer_status` (str, e.g. `"fully_answered"`)
    - `related_users[]`: `[{id, response, user: {id, first_name, last_name, full_name, ...}}]`
      - `response` values: `"attended"` (accepted), `"declined"`, `"not_answered"`
- **`GET /app/events/{event_id}/occurrences/{occurrence_id}/`** (`version=12`)
  - Full occurrence details:
    - `title`, `description`, `event_type`, `start`, `end`, `meetup_time`, `response_deadline`
    - `location`, `event_location`, `meetup_location` (`{id, name, lat, lng}`)
    - `group` (`{id, name}`), `cancelled` (bool), `cancelled_reason` (str | null)
    - `home_team_name`, `away_team_name`, `home_team_score`, `away_team_score`, `tournament_name`
    - `invites`: `{participants_total, participants_accepted, participants_declined, participants_waiting_list, member_invites_total, ...}`
    - `attachments[]`

### 4. Attendance & RSVP

- **`GET /app/events/{event_id}/occurrences/{occurrence_id}/my/responses/`** (`version=1`)
  - Lists all controllable `organizers[]` and `participants[]` for the occurrence:
    - `id`, `user_id`, `occurrence_id`, `event_id`, `user` (`{id, first_name, last_name, full_name}`)
    - `response` (int): `0` = not answered, `1` = declined, `2` = accepted (`attended`)
    - `response_reason` (str), `attendance` (int), `attendance_note` (str), `can_edit_response` (bool)
- **`GET /app/events/{event_id}/occurrences/{occurrence_id}/responses/{user_id}/`** (`version=4`)
  - Single controllable member's response detail.
- **`PATCH /app/events/{event_id}/occurrences/{occurrence_id}/responses/{user_id}/`** (`version=4`)
  - Updates a member's RSVP response:
    - Accept: `{"response": 2}`
    - Decline: `{"response": 1, "response_reason": "<optional reason>"}`

### 5. Updates / Notifications

- **`GET /app/users/current/notifications/?page={page}&pagination_count=false&occurrence_version=19&occurrence_response_version=1&occurrence_reminder_version=1&occurrence_comment_version=1&payment_version=7&post_version=5&comment_version=4&poll_version=1&poll_comment_version=1`** (`version=10`)
  - Read-only notification feed (`has_next`, `results[]`).
  - **Note**: The `*_version` query parameters are required; without them, `data` is returned empty (`{}`).
  - Each item: `{id, timestamp, is_accessed, is_seen, data: {title, body, type, action, EXTRA_GROUP_ID, EXTRA_GROUP_NAME, ...}}`.
  - Respects `Accept-Language: no` for Norwegian text.

### 6. Posts & Comments

- **`GET /app/users/current/posts/?page={page}&pagination_count=false`** (`version=5`)
  - Paginated group wall posts (`has_next`, `results[]`):
    - `id`, `title`, `text`, `poster_id`, `poster_name`, `created_at`, `updated_at`
    - `group_id`, `group_name`, `parent_group_name`, `event_id`, `occurrence_id`, `event_name`
    - `comment_count`, `likes_count`, `seen_by_count`, `file_count`, `attachments[]`
- **`GET /app/posts/{post_id}/`** (`version=6`)
  - Full post detail including `attachments[]` (`[{id, url}]` — presigned S3 URLs).
- **`GET /app/posts/{post_id}/comments/?page={page}&pagination_count=false`** (`version=4`)
  - Paginated comments for a post (`has_next`, `results[]`).

### 7. Files & Attachments

- **`GET /app/drive/my/files/?page={page}&pagination_count=false`** (`version=1`)
  - Paginated Drive files accessible to the user (`id`, `name`, `file_type`, `mime_type`, `size`, `last_modified`, `group_path`).
- **`GET /app/drive/files/{file_id}/`** (`version=1`)
  - Detailed metadata for a Drive file (`name`, `file_type`, `mime_type`, `size`, `group`, `last_modified_by`, `last_modified_at`).
- Post attachments (`GET /app/posts/{post_id}/`) and event attachments (`GET /app/events/{event_id}/occurrences/{occurrence_id}/`) include direct presigned S3 URLs when present.

---

## Stream Chat API (`https://chat.stream-io-api.com`)

Hoopit delegates chat to GetStream Chat using `api_key=y8afyfq3u8a5` and the JWT from `GET /app/users/current/tokens/`.

- **Headers**:
  - `Authorization: <stream_chat_token>`
  - `Stream-Auth-Type: jwt`
- **List Channels** (read-only, `watch: false`, `presence: false`):
  - `GET /channels?api_key=y8afyfq3u8a5&user_id={uuid}&payload={url_encoded_json}`
  - Payload:
    ```json
    {
      "state": true,
      "watch": false,
      "presence": false,
      "sort": [{"field": "last_message_at", "direction": -1}],
      "filter_conditions": {
        "$and": [
          {"members": {"$in": ["<user_uuid>"]}},
          {"type": {"$in": ["messaging", "guardians"]}},
          {"last_message_at": {"$gt": "2000-01-01T00:00:00.00Z"}}
        ]
      },
      "message_limit": 1,
      "limit": 20
    }
    ```
- **Query Channel Messages** (read-only, `watch: false`, `presence: false`):
  - `POST /channels/{channel_type}/{channel_id}/query?api_key=y8afyfq3u8a5&user_id={uuid}`
  - Body: `{"state": true, "watch": false, "presence": false, "messages": {"limit": 20}}`
  - Returns `channel`, `members[]`, `messages[]` (oldest first), `read[]`.

---

## Spond MCP Feature Parity Mapping

| Spond MCP Tool | Hoopit Support | Notes |
| --- | --- | --- |
| `list_groups` | Supported | `GET /app/users/current/groups/` |
| `get_upcoming_events` | Supported | `GET /app/users/current/events/` (paginated, date/group/member filtered) |
| `get_event_details` | Supported | `GET /app/events/{event_id}/occurrences/{occ_id}/` + `/my/responses/` |
| `get_attendance` | Supported | Upcoming events `related_users[]` response status (`attended`/`declined`/`not_answered`) |
| `respond_to_event` | Supported | `PATCH /app/events/{event_id}/occurrences/{occ_id}/responses/{user_id}/` |
| `search_events` | Supported | Client-side search across upcoming events (title, location, group) |
| `get_updates` | Supported | `GET /app/users/current/notifications/` (with `*_version` params) |
| `get_posts` | Supported | `GET /app/users/current/posts/` |
| `get_post_comments` | Supported | `GET /app/posts/{post_id}/` + `GET /app/posts/{post_id}/comments/` |
| `get_chats` | Supported | Stream Chat `GET /channels` (`watch=false`) |
| `get_chat_messages` | Supported | Stream Chat `POST /channels/{type}/{id}/query` (`watch=false`) |
| `get_attachment` | Supported | Post/event attachments (presigned URLs) + Drive files (`GET /app/drive/files/{id}/` & `list_files`) |
