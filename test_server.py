"""Unit tests for HoopitService and all 12 MCP tools (mocked client, synthetic data)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from server import HoopitService, handle_tool_call, parse_event_id


def _iso_in(days: float, hours: float = 0) -> str:
    return (datetime.now(UTC) + timedelta(days=days, hours=hours)).isoformat()


def _make_mock_client() -> AsyncMock:
    client = AsyncMock()
    client.get_groups.return_value = [
        {
            "id": 100,
            "name": "Jenter 2016",
            "organization_name": "Fjordvik IL",
            "division_name": "Fotball",
            "members": [{"id": 10, "first_name": "Emma"}],
        },
        {
            "id": 101,
            "name": "Gutter 2014",
            "organization_name": "Fjordvik IL",
            "division_name": "Håndball",
            "members": [{"id": 11, "first_name": "Oliver"}],
        },
    ]
    client.get_events.return_value = [
        {
            "id": 1800000000,
            "event_id": 500,
            "title": "Trening tirsdag",
            "datetime": _iso_in(2),
            "end": _iso_in(2, hours=1.5),
            "location": "Kunstgresset",
            "event_type": "training",
            "group": {"id": 100, "name": "Jenter 2016"},
            "accepted_participant_count": 10,
            "declined_participant_count": 2,
            "not_responded_participant_count": 3,
            "related_users": [
                {"id": 10, "response": "not_answered", "user": {"id": 10, "first_name": "Emma", "full_name": "Emma N"}}
            ],
        },
        {
            "id": 1800090000,
            "event_id": 501,
            "title": "Vintercup kamp",
            "datetime": _iso_in(5),
            "end": _iso_in(5, hours=2),
            "location": "Idrettshallen",
            "event_type": "competition",
            "group": {"id": 101, "name": "Gutter 2014"},
            "accepted_participant_count": 12,
            "declined_participant_count": 1,
            "not_responded_participant_count": 0,
            "related_users": [
                {"id": 11, "response": "attended", "user": {"id": 11, "first_name": "Oliver", "full_name": "Oliver N"}}
            ],
        },
    ]
    client.get_event_details.return_value = {
        "id": 1800000000,
        "event": 500,
        "title": "Trening tirsdag",
        "event_type": "training",
        "start": _iso_in(2),
        "end": _iso_in(2, hours=1.5),
        "meetup_time": _iso_in(2, hours=-0.25),
        "response_deadline": _iso_in(1),
        "cancelled": False,
        "cancelled_reason": None,
        "description": "Husk leggskinn og vannflaske.",
        "group": {"id": 100, "name": "Jenter 2016"},
        "location": {"name": "Kunstgresset"},
        "meetup_location": {"name": "Inngang nord"},
        "invites": {"participants_accepted": 10, "participants_declined": 2, "participants_total": 15},
        "not_responded_count": 3,
        "attachments": [{"id": 99, "name": "Treningsplan.pdf", "url": "https://example.test/plan.pdf"}],
    }
    client.get_my_responses.return_value = {
        "organizers": [],
        "participants": [
            {
                "id": 10,
                "user_id": 10,
                "response": 0,
                "response_reason": "",
                "user": {"id": 10, "first_name": "Emma", "full_name": "Emma N"},
            }
        ],
    }
    client.respond_to_event.return_value = {"id": 10, "response": 2}
    client.get_notifications.return_value = [
        {
            "id": 700,
            "timestamp": _iso_in(0, hours=-2),
            "is_seen": False,
            "data": {
                "title": "Ny invitasjon",
                "body": "Emma er invitert til Trening tirsdag",
                "EXTRA_GROUP_NAME": "Jenter 2016",
            },
        },
        {
            "id": 701,
            "timestamp": _iso_in(-5),
            "is_seen": True,
            "data": {"title": "Gammelt varsel", "body": "Utenfor vindu", "EXTRA_GROUP_NAME": "Gutter 2014"},
        },
    ]
    client.get_posts.return_value = [
        {
            "id": 800,
            "title": "Kampoppsett helgen",
            "text": "Vi møtes 30 minutter før kampstart.",
            "poster_name": "Trener Kari",
            "group_name": "Jenter 2016",
            "created_at": _iso_in(-1),
            "comment_count": 1,
            "likes_count": 4,
            "file_count": 1,
            "attachments": [{"id": 1, "url": "https://example.test/img.jpg"}],
        }
    ]
    client.get_post.return_value = {
        "id": 800,
        "title": "Kampoppsett helgen",
        "text": "Vi møtes 30 minutter før kampstart.",
        "poster_name": "Trener Kari",
        "group_name": "Jenter 2016",
        "created_at": _iso_in(-1),
        "attachments": [{"id": 1, "url": "https://example.test/img.jpg"}],
    }
    client.get_post_comments.return_value = [
        {"user_name": "Forelder Per", "created_at": _iso_in(0, hours=-3), "text": "Vi tar med frukt!"}
    ]
    client.get_chats.return_value = [
        {
            "channel": {
                "cid": "messaging:chan-1",
                "type": "messaging",
                "id": "chan-1",
                "name": "Jenter 2016 Foreldre",
                "last_message_at": _iso_in(0, hours=-1),
            },
            "members": [{"user": {"name": "Kari"}}, {"user": {"name": "Per"}}],
            "messages": [{"user": {"name": "Kari"}, "text": "Husk drakt i morgen!"}],
            "read": [{"unread_messages": 2}],
        }
    ]
    client.get_chat_messages.return_value = {
        "channel": {"cid": "messaging:chan-1", "name": "Jenter 2016 Foreldre"},
        "messages": [
            {"user": {"name": "Kari"}, "created_at": _iso_in(0, hours=-2), "text": "Hei alle!"},
            {"user": {"name": "Per"}, "created_at": _iso_in(0, hours=-1), "text": "Takk for info!"},
        ],
    }
    client.get_drive_files.return_value = [
        {
            "id": 900,
            "name": "Dugnadsliste.pdf",
            "file_type": "pdf",
            "mime_type": "application/pdf",
            "size": 102400,
            "group_path": [{"name": "Fjordvik IL"}],
            "last_modified": _iso_in(-3),
        }
    ]
    client.get_drive_file.return_value = {
        "id": 900,
        "name": "Dugnadsliste.pdf",
        "file_type": "pdf",
        "mime_type": "application/pdf",
        "size": 102400,
        "group": {"name": "Fjordvik IL"},
        "last_modified_by": {"full_name": "Trener Kari"},
        "last_modified_at": _iso_in(-3),
    }
    return client


def test_parse_event_id():
    assert parse_event_id("500:1800000000") == (500, 1800000000)
    assert parse_event_id("500/1800000000") == (500, 1800000000)
    with pytest.raises(ValueError):
        parse_event_id("invalid")
    with pytest.raises(ValueError):
        parse_event_id("abc:123")


async def test_list_groups():
    svc = HoopitService(_make_mock_client())
    out = await handle_tool_call(svc, "list_groups", {})
    assert "Hoopit-grupper (2):" in out
    assert "Jenter 2016" in out
    assert "Gutter 2014" in out


async def test_get_upcoming_events_and_filters():
    svc = HoopitService(_make_mock_client())
    all_out = await handle_tool_call(svc, "get_upcoming_events", {"days": 7})
    assert "Trening tirsdag" in all_out
    assert "Vintercup kamp" in all_out

    filtered = await handle_tool_call(svc, "get_upcoming_events", {"days": 7, "group_name": "Jenter"})
    assert "Trening tirsdag" in filtered
    assert "Vintercup kamp" not in filtered

    by_member = await handle_tool_call(svc, "get_upcoming_events", {"days": 7, "member_name": "Oliver"})
    assert "Vintercup kamp" in by_member
    assert "Trening tirsdag" not in by_member


async def test_get_event_details():
    svc = HoopitService(_make_mock_client())
    out = await handle_tool_call(svc, "get_event_details", {"event_id": "500:1800000000"})
    assert "Trening tirsdag (Trening)" in out
    assert "Husk leggskinn og vannflaske." in out
    assert "Treningsplan.pdf" in out
    assert "Emma N: ❓ Ikke svart" in out


async def test_get_attendance():
    svc = HoopitService(_make_mock_client())
    out = await handle_tool_call(svc, "get_attendance", {"days": 14})
    assert "Mangler svar (1)" in out
    assert "Trening tirsdag" in out
    assert "Besvarte aktiviteter (1)" in out
    assert "Vintercup kamp" in out


async def test_respond_to_event_accept_and_decline():
    client = _make_mock_client()
    svc = HoopitService(client)

    out_acc = await handle_tool_call(svc, "respond_to_event", {"event_id": "500:1800000000", "accept": True})
    assert "Emma N" in out_acc
    assert "✅ Kommer" in out_acc
    client.respond_to_event.assert_awaited_once_with(500, 1800000000, 10, accept=True, decline_reason="")

    client.respond_to_event.return_value = {"id": 10, "response": 1}
    out_dec = await handle_tool_call(
        svc,
        "respond_to_event",
        {"event_id": "500:1800000000", "accept": False, "member_name": "Emma", "decline_message": "Ferie"},
    )
    assert "❌ Kan ikke" in out_dec
    assert "Ferie" in out_dec


async def test_respond_to_event_ambiguous_members():
    client = _make_mock_client()
    client.get_my_responses.return_value = {
        "organizers": [],
        "participants": [
            {"id": 10, "user_id": 10, "user": {"id": 10, "full_name": "Emma N"}},
            {"id": 11, "user_id": 11, "user": {"id": 11, "full_name": "Oliver N"}},
        ],
    }
    svc = HoopitService(client)
    with pytest.raises(ValueError, match="Flere medlemmer"):
        await handle_tool_call(svc, "respond_to_event", {"event_id": "500:1800000000", "accept": True})


async def test_search_events():
    svc = HoopitService(_make_mock_client())
    out = await handle_tool_call(svc, "search_events", {"query": "cup"})
    assert "Vintercup kamp" in out
    assert "Trening tirsdag" not in out


async def test_get_updates():
    svc = HoopitService(_make_mock_client())
    out = await handle_tool_call(svc, "get_updates", {"since_hours": 48})
    assert "Ny invitasjon" in out
    assert "Gammelt varsel" not in out


async def test_get_posts_and_post_comments():
    svc = HoopitService(_make_mock_client())
    posts_out = await handle_tool_call(svc, "get_posts", {"group_name": "Jenter"})
    assert "Kampoppsett helgen" in posts_out
    assert "Trener Kari" in posts_out

    comments_out = await handle_tool_call(svc, "get_post_comments", {"post_id": 800})
    assert "Forelder Per" in comments_out
    assert "Vi tar med frukt!" in comments_out
    assert "https://example.test/img.jpg" in comments_out


async def test_get_chats_and_chat_messages():
    svc = HoopitService(_make_mock_client())
    chats_out = await handle_tool_call(svc, "get_chats", {"max_chats": 5})
    assert "Jenter 2016 Foreldre" in chats_out
    assert "2 uleste" in chats_out
    assert "messaging:chan-1" in chats_out

    msgs_out = await handle_tool_call(svc, "get_chat_messages", {"chat_id": "messaging:chan-1", "limit": 10})
    assert "Hei alle!" in msgs_out
    assert "Takk for info!" in msgs_out


async def test_get_attachment_sources():
    svc = HoopitService(_make_mock_client())
    drive_list = await handle_tool_call(svc, "get_attachment", {"source": "drive"})
    assert "Dugnadsliste.pdf" in drive_list

    drive_one = await handle_tool_call(svc, "get_attachment", {"source": "drive", "item_id": "900"})
    assert "100.0 KB" in drive_one
    assert "Trener Kari" in drive_one

    post_att = await handle_tool_call(svc, "get_attachment", {"source": "post", "item_id": "800"})
    assert "https://example.test/img.jpg" in post_att

    ev_att = await handle_tool_call(svc, "get_attachment", {"source": "event", "item_id": "500:1800000000"})
    assert "Treningsplan.pdf" in ev_att


async def test_unknown_tool():
    svc = HoopitService(_make_mock_client())
    out = await handle_tool_call(svc, "does_not_exist", {})
    assert "Ukjent verktøy" in out
