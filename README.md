# hoopit-mcp

An [MCP](https://modelcontextprotocol.io/) server for [Hoopit](https://hoopit.io/) sports team and activity management, modeled after [`spond-no-match-mcp`](../spond-no-match-mcp). Gives AI assistants read-only access to groups, upcoming trainings/matches, attendance, posts, notifications, chats, and Drive files — plus RSVP support.

## Features

- **12 MCP tools** — groups, upcoming events, event details, attendance, RSVP, search, updates, posts, comments, chats, chat messages, and attachments/Drive files
- **Norwegian output** — responses formatted in Norwegian (`Europe/Oslo` timezone, Norwegian weekdays and status labels)
- **TTL caching** — groups & user profile cached 1h, events/posts/files 5min, chats 1min (automatically invalidated on RSVP)
- **Optional bearer auth** — set `MCP_AUTH_TOKEN` to protect `/sse` and `/messages/`; `/health` is always open
- **Read-only by default** — all tools except `respond_to_event` are strictly read-only and never mark notifications or chats as read

## Tools

| Tool | Description |
| --- | --- |
| `list_groups` | List all Hoopit groups with club, division, and members |
| `get_upcoming_events` | Upcoming events, filterable by `days`, `from_days`, `group_name`, or `member_name` |
| `get_event_details` | Full details for an event (`<event_id>:<occurrence_id>`): description, location, meetup, deadline, RSVP |
| `get_attendance` | Upcoming events grouped by unanswered vs. answered responses |
| `respond_to_event` | Accept or decline an event for yourself or a child (`event_id`, `accept`, optional `member_name` & `decline_message`) |
| `search_events` | Search upcoming events by title, location, type, or group |
| `get_updates` | Notification feed (newest first, filterable by group and `since_hours`) |
| `get_posts` | Group wall posts with comment/like/attachment counts |
| `get_post_comments` | Full post text, attachment links, and all comments |
| `get_chats` | Recent chats (direct, group, guardian) via Stream Chat with last message and unread count |
| `get_chat_messages` | Message history for one chat (`<type>:<id>`), oldest first |
| `get_attachment` | List/inspect Hoopit Drive files (`source="drive"`), or fetch attachment links for a `post` or `event` |

## Quick Start

### Prerequisites

- Python 3.11+
- A Hoopit account (authenticated via Firebase refresh token, email/password, or Firebase ID token)

### Install & Run Locally

```bash
pip install -r requirements.txt

HOOPIT_REFRESH_TOKEN=your-firebase-refresh-token \
MCP_AUTH_TOKEN=optional-bearer-token \
python main.py
```

The server starts on port `8080`:

- Health check: `http://localhost:8080/health`
- MCP SSE endpoint: `http://localhost:8080/sse`

### Environment Variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `HOOPIT_REFRESH_TOKEN` | One of auth vars | | Firebase refresh token (recommended — works for phone OTP, Vipps, NIF, and email accounts; auto-refreshes hourly) |
| `HOOPIT_EMAIL` | With `HOOPIT_PASSWORD` | | Hoopit/Firebase account email (if email/password sign-in is enabled) |
| `HOOPIT_PASSWORD` | With `HOOPIT_EMAIL` | | Hoopit/Firebase account password |
| `HOOPIT_ID_TOKEN` | Fallback | | Direct Firebase ID token (valid ~1 hour; cannot auto-refresh without a refresh token) |
| `HOOPIT_FIREBASE_API_KEY` | No | App default | Override Firebase Web/Android API key if needed |
| `MCP_AUTH_TOKEN` | No | *(open)* | Optional static bearer token required on `/sse` and `/messages/` (`Authorization: Bearer <token>`) |
| `PORT` | No | `8080` | HTTP server port |

## Usage with mcporter

```json
{
  "mcpServers": {
    "hoopit": {
      "url": "http://localhost:8080/sse",
      "transport": "sse",
      "headers": {
        "Authorization": "Bearer your-mcp-auth-token"
      }
    }
  }
}
```

## Architecture

```text
MCP Client (mcporter, Claude, etc.)
    │ HTTP/SSE (JSON-RPC)
    ▼
hoopit-mcp (:8080)
    ├── securetoken.googleapis.com   (Firebase ID token refresh)
    ├── api.hoopit.io                (Groups, events, RSVP, posts, notifications, Drive)
    └── chat.stream-io-api.com       (Read-only Stream Chat REST API)
```

See [`docs/HOOPIT_API.md`](docs/HOOPIT_API.md) for the full unofficial API specification.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt

# Run unit tests (all writes/RSVPs are mocked)
pytest -v

# Lint
ruff check .
```
