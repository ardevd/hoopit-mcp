"""Unit tests for HoopitService and all 12 MCP tools (mocked client, synthetic data)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from server import HoopitService, format_amount, fuzzy_match, handle_tool_call, parse_event_id


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
    client.get_drive_file_download.return_value = {
        "id": 900,
        "download_url": "https://cdn.example.test/dugnadsliste.pdf",
    }
    client.get_event_comments.return_value = [
        {
            "id": 1,
            "comment": "Husk å ta med overtrekksvest!",
            "poster": {"full_name": "Trener Kari"},
            "timestamp": _iso_in(0, hours=-4),
            "like_count": 3,
            "latest_replies": [
                {
                    "comment": "Notert!",
                    "poster": {"full_name": "Forelder Per"},
                    "timestamp": _iso_in(0, hours=-3),
                }
            ],
        }
    ]
    client.get_event_invites.return_value = [
        {"response": 2, "user": {"full_name": "Emma N"}, "jersey_number": "7", "response_reason": ""},
        {"response": 1, "user": {"full_name": "Oliver N"}, "jersey_number": None, "response_reason": "Syk"},
        {"response": 0, "user": {"full_name": "Maja S"}, "waiting_list_position": 1},
    ]
    client.get_event_tasks.return_value = [
        {
            "id": 44,
            "title": "Kioskvakt 1. omgang",
            "description": "Steke vafler og selge kaffe.",
            "accepted_count": 1,
            "needed_count": 2,
            "assignees": [{"user": {"full_name": "Forelder Per"}, "response": 2}],
        }
    ]
    client.get_pending_payments.return_value = [
        {"id": 301, "payment_name": "Treningsavgift vår", "amount": 125000, "currency": "NOK", "due_date": _iso_in(10)}
    ]
    client.get_payment_plans.return_value = [{"id": 401, "payment_name": "Årskontingent delbetaling"}]
    client.get_expense_claims.return_value = [
        {"id": 501, "title": "Dommerutlegg", "status": "pending", "amount": 45050}
    ]
    client.get_payment_history.return_value = [
        {
            "id": 601,
            "payment_name": "Vintercup påmelding",
            "charge_made": _iso_in(-14),
            "group": {"name": "Jenter 2016"},
            "transaction_participants": [{"id": 10, "full_name": "Emma N"}],
        }
    ]
    client.get_payment_detail.return_value = {
        "id": 601,
        "payment_name": "Vintercup påmelding",
        "currency": "NOK",
        "payment_sum": 35000,
        "refunded_sum": 0,
        "total_sum": 35000,
        "charge_made": _iso_in(-14),
        "group": {"name": "Jenter 2016"},
        "description": "Påmeldingsavgift til cup.",
        "receipt_url": "https://receipts.example.test/601",
        "user_payments": [{"id": 1, "user": {"full_name": "Emma N"}, "amount": 35000}],
    }
    client.get_inbox.return_value = [
        {
            "type": "task",
            "title": "Dugnad vakt søndag",
            "group": {"name": "Jenter 2016"},
            "datetime": _iso_in(3),
        }
    ]
    return client


def test_fuzzy_match_rejects_empty_text():
    assert fuzzy_match("", "Jenter 2016") is False
    assert fuzzy_match("   ", "Jenter 2016") is False
    assert fuzzy_match("Jenter 2016", "") is True
    assert fuzzy_match("Jenter 2016", "jenter") is True


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
    assert "https://cdn.example.test/dugnadsliste.pdf" in drive_one

    post_att = await handle_tool_call(svc, "get_attachment", {"source": "post", "item_id": "800"})
    assert "https://example.test/img.jpg" in post_att

    ev_att = await handle_tool_call(svc, "get_attachment", {"source": "event", "item_id": "500:1800000000"})
    assert "Treningsplan.pdf" in ev_att


async def test_get_chats_filters_unread_to_current_user():
    client = _make_mock_client()
    client.user_uuid = "me-uuid"
    client.get_chats.return_value = [
        {
            "channel": {
                "cid": "messaging:chan-2",
                "type": "messaging",
                "id": "chan-2",
                "name": "Stor foreldregruppe",
                "last_message_at": _iso_in(0, hours=-1),
            },
            "members": [{"user": {"id": "me-uuid", "name": "Meg"}}, {"user": {"id": "other-uuid", "name": "Per"}}],
            "messages": [{"user": {"name": "Per"}, "text": "Hei"}],
            "read": [
                {"user": {"id": "me-uuid"}, "unread_messages": 0},
                {"user": {"id": "other-uuid"}, "unread_messages": 17},
            ],
        }
    ]
    svc = HoopitService(client)
    out = await handle_tool_call(svc, "get_chats", {"max_chats": 5})
    assert "Stor foreldregruppe" in out
    assert "17 uleste" not in out
    assert "uleste" not in out


async def test_respond_to_event_rejects_locked_response():
    client = _make_mock_client()
    client.get_my_responses.return_value = {
        "organizers": [],
        "participants": [
            {
                "id": 10,
                "user_id": 10,
                "can_edit_response": False,
                "user": {"id": 10, "full_name": "Emma N"},
            }
        ],
    }
    svc = HoopitService(client)
    with pytest.raises(ValueError, match="Kan ikke endre svar"):
        await handle_tool_call(svc, "respond_to_event", {"event_id": "500:1800000000", "accept": True})


def test_format_amount():
    assert format_amount(50000, "NOK") == "500 kr"
    assert format_amount(45050, "NOK") == "450,50 kr"
    assert format_amount(1250, "EUR") == "12.50 EUR"
    assert format_amount(None) == "ukjent beløp"


async def test_get_event_comments_participants_and_tasks():
    svc = HoopitService(_make_mock_client())

    comments_out = await handle_tool_call(svc, "get_event_comments", {"event_id": "500:1800000000"})
    assert "Trener Kari" in comments_out
    assert "overtrekksvest" in comments_out
    assert "Forelder Per" in comments_out
    assert "👍 3" in comments_out

    all_parts = await handle_tool_call(svc, "get_event_participants", {"event_id": "500:1800000000"})
    assert "Emma N #7" in all_parts
    assert "Oliver N (Syk)" in all_parts
    assert "Maja S [venteliste #1]" in all_parts

    only_accepted = await handle_tool_call(
        svc,
        "get_event_participants",
        {"event_id": "500:1800000000", "status": "accepted"},
    )
    assert "Emma N #7" in only_accepted
    assert "Oliver N" not in only_accepted

    tasks_out = await handle_tool_call(svc, "get_event_tasks", {"event_id": "500:1800000000"})
    assert "Kioskvakt 1. omgang" in tasks_out
    assert "(1/2 bekreftet)" in tasks_out
    assert "Forelder Per: ✅ Kommer" in tasks_out


async def test_get_payments_overview_and_receipt():
    svc = HoopitService(_make_mock_client())

    overview = await handle_tool_call(svc, "get_payments", {"include_history": True})
    assert "Treningsavgift vår" in overview
    assert "1250 kr" in overview
    assert "Årskontingent delbetaling" in overview
    assert "Dommerutlegg" in overview
    assert "450,50 kr" in overview
    assert "Vintercup påmelding" in overview

    no_hist = await handle_tool_call(svc, "get_payments", {"include_history": False})
    assert "Treningsavgift vår" in no_hist
    assert "Vintercup påmelding" not in no_hist

    receipt = await handle_tool_call(svc, "get_payments", {"payment_id": 601})
    assert "350 kr" in receipt
    assert "https://receipts.example.test/601" in receipt
    assert "Emma N: 350 kr" in receipt


async def test_get_inbox():
    client = _make_mock_client()
    svc = HoopitService(client)
    out = await handle_tool_call(svc, "get_inbox", {"max_items": 10})
    assert "Dugnad vakt søndag" in out
    assert "Jenter 2016" in out

    client.get_inbox.return_value = []
    empty = await handle_tool_call(svc, "get_inbox", {})
    assert "Innboksen er tom" in empty


async def test_unknown_tool():
    svc = HoopitService(_make_mock_client())
    out = await handle_tool_call(svc, "does_not_exist", {})
    assert "Ukjent verktøy" in out
