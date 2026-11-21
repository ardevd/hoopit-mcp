"""Hoopit MCP server — service layer, tool definitions, and Norwegian formatting."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from hoopit_client import (
    RESPONSE_ACCEPTED,
    RESPONSE_DECLINED,
    HoopitClient,
)

logger = logging.getLogger(__name__)

WEEKDAYS_NO = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
LOCAL_TZ = ZoneInfo("Europe/Oslo")

EVENT_TYPE_NO = {
    "training": "Trening",
    "competition": "Kamp",
    "match": "Kamp",
    "meeting": "Møte",
    "dugnad": "Dugnad",
    "social": "Sosialt",
    "cup": "Cup",
    "other": "Aktivitet",
}

RESPONSE_STATUS_STR_NO = {
    "attended": "✅ Kommer",
    "accepted": "✅ Kommer",
    "declined": "❌ Kan ikke",
    "not_answered": "❓ Ikke svart",
    "not_responded": "❓ Ikke svart",
    "waiting_list": "⏳ Venteliste",
}

RESPONSE_STATUS_INT_NO = {
    0: "❓ Ikke svart",
    1: "❌ Kan ikke",
    2: "✅ Kommer",
}


def parse_timestamp(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(LOCAL_TZ)
    except (ValueError, TypeError):
        return None


def norwegian_weekday(weekday: int) -> str:
    return WEEKDAYS_NO[weekday]


def format_datetime_no(dt: datetime | None, end_dt: datetime | None = None) -> str:
    if dt is None:
        return "Ukjent tidspunkt"
    day_str = f"{norwegian_weekday(dt.weekday()).capitalize()} {dt.strftime('%d.%m.%Y')}"
    start_time = dt.strftime("%H:%M")
    if end_dt is not None:
        if end_dt.date() == dt.date():
            return f"{day_str} kl. {start_time}–{end_dt.strftime('%H:%M')}"
        return f"{day_str} kl. {start_time} – {end_dt.strftime('%d.%m.%Y')} kl. {end_dt.strftime('%H:%M')}"
    return f"{day_str} kl. {start_time}"


def format_event_id(event_id: int | str, occurrence_id: int | str) -> str:
    return f"{event_id}:{occurrence_id}"


def parse_event_id(raw: str) -> tuple[int, int]:
    """Parse 'event_id:occurrence_id' (or 'event_id/occurrence_id') into ints."""
    cleaned = (raw or "").strip()
    sep = ":" if ":" in cleaned else ("/" if "/" in cleaned else "")
    if not sep:
        raise ValueError("Ugyldig event_id-format. Bruk '<event_id>:<occurrence_id>' fra get_upcoming_events.")
    left, right = cleaned.split(sep, 1)
    try:
        return int(left.strip()), int(right.strip())
    except ValueError as e:
        raise ValueError("Ugyldig event_id-format. Begge deler må være heltall ('<event_id>:<occurrence_id>').") from e


def fuzzy_match(text: str, query: str) -> bool:
    t = (text or "").strip().lower()
    q = (query or "").strip().lower()
    if not q:
        return True
    if not t:
        return False
    return q in t or t in q


def format_response_status(val: Any) -> str:
    if isinstance(val, int):
        return RESPONSE_STATUS_INT_NO.get(val, f"Ukjent ({val})")
    if isinstance(val, str):
        return RESPONSE_STATUS_STR_NO.get(val.lower(), val)
    return "❓ Ikke svart"


def format_event_type(raw: str | None) -> str:
    if not raw:
        return "Aktivitet"
    return EVENT_TYPE_NO.get(raw.lower(), raw.capitalize())


def format_size(size_bytes: int | None) -> str:
    if not size_bytes:
        return "ukjent størrelse"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _event_user_name(user: dict[str, Any] | None) -> str:
    if not user:
        return "Ukjent"
    full = (user.get("full_name") or "").strip()
    if full:
        return full
    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    return f"{first} {last}".strip() or "Ukjent"


class HoopitService:
    """Service layer wrapping HoopitClient with filtering, member resolution, and formatting."""

    def __init__(self, client: HoopitClient) -> None:
        self._client = client

    async def close(self) -> None:
        await self._client.close()

    def clear_cache(self) -> None:
        self._client.clear_cache()

    @property
    def user_uuid(self) -> str | None:
        val = getattr(self._client, "user_uuid", None)
        return val if isinstance(val, str) and val else None

    async def get_groups(self) -> list[dict[str, Any]]:
        return await self._client.get_groups()

    async def get_upcoming_events(
        self,
        *,
        days: int = 7,
        from_days: int = 0,
        group_name: str | None = None,
        member_name: str | None = None,
    ) -> list[dict[str, Any]]:
        max_pages = 3 if (from_days + days) <= 30 else 6
        raw_events = await self._client.get_events(max_pages=max_pages)
        now = datetime.now(UTC)
        window_start = now + timedelta(days=from_days)
        window_end = now + timedelta(days=from_days + days)

        filtered: list[dict[str, Any]] = []
        for ev in raw_events:
            dt = parse_timestamp(ev.get("datetime"))
            if dt is not None:
                dt_utc = dt.astimezone(UTC)
                if dt_utc < window_start or dt_utc > window_end:
                    continue
            if group_name:
                gname = (ev.get("group") or {}).get("name", "")
                if not fuzzy_match(gname, group_name):
                    continue
            if member_name:
                related = ev.get("related_users") or []
                if not any(fuzzy_match(_event_user_name(ru.get("user")), member_name) for ru in related):
                    continue
            filtered.append(ev)

        filtered.sort(key=lambda e: e.get("datetime") or "")
        return filtered

    async def get_event_details(self, event_id_str: str) -> tuple[dict[str, Any], dict[str, Any]]:
        eid, oid = parse_event_id(event_id_str)
        detail, my_responses = await asyncio.gather(
            self._client.get_event_details(eid, oid),
            self._client.get_my_responses(eid, oid),
        )
        return detail, my_responses

    async def respond_to_event(
        self,
        event_id_str: str,
        accept: bool,
        member_name: str | None = None,
        decline_message: str = "",
    ) -> tuple[str, dict[str, Any]]:
        eid, oid = parse_event_id(event_id_str)
        my_resp = await self._client.get_my_responses(eid, oid)
        controllable = [*(my_resp.get("participants") or []), *(my_resp.get("organizers") or [])]
        if not controllable:
            raise ValueError("Fant ingen medlemmer du kan svare for på denne aktiviteten.")

        target: dict[str, Any] | None = None
        if member_name:
            for item in controllable:
                if fuzzy_match(_event_user_name(item.get("user")), member_name):
                    target = item
                    break
            if target is None:
                names = ", ".join(_event_user_name(i.get("user")) for i in controllable)
                raise ValueError(f"Fant ikke '{member_name}' blant inviterte du kan svare for ({names}).")
        elif len(controllable) == 1:
            target = controllable[0]
        else:
            names = ", ".join(_event_user_name(i.get("user")) for i in controllable)
            raise ValueError(
                f"Flere medlemmer er invitert ({names}). Oppgi member_name for å velge hvem du svarer for."
            )

        assert target is not None
        user_id = target.get("user_id") or (target.get("user") or {}).get("id") or target.get("id")
        if not user_id:
            raise ValueError("Mangler bruker-ID for medlemmet.")
        resolved_name = _event_user_name(target.get("user"))
        if target.get("can_edit_response") is False:
            raise ValueError(
                f"Kan ikke endre svar for {resolved_name} på denne aktiviteten "
                "(svarfristen kan være utløpt eller låst)."
            )
        result = await self._client.respond_to_event(
            eid,
            oid,
            int(user_id),
            accept=accept,
            decline_reason=decline_message,
        )
        return resolved_name, result

    async def get_updates(
        self,
        *,
        group_name: str | None = None,
        since_hours: int = 48,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        raw = await self._client.get_notifications(max_pages=2)
        cutoff = datetime.now(UTC) - timedelta(hours=since_hours)
        out: list[dict[str, Any]] = []
        for n in raw:
            dt = parse_timestamp(n.get("timestamp"))
            if dt is not None and dt.astimezone(UTC) < cutoff:
                continue
            data = n.get("data") or {}
            if group_name:
                gname = data.get("EXTRA_GROUP_NAME") or ""
                if not fuzzy_match(gname, group_name):
                    continue
            out.append(n)
            if len(out) >= limit:
                break
        return out

    async def get_posts(
        self,
        *,
        group_name: str | None = None,
        max_posts: int = 10,
        since_days: int = 14,
    ) -> list[dict[str, Any]]:
        raw = await self._client.get_posts(max_pages=3)
        cutoff = datetime.now(UTC) - timedelta(days=since_days)
        out: list[dict[str, Any]] = []
        for p in raw:
            dt = parse_timestamp(p.get("created_at"))
            if dt is not None and dt.astimezone(UTC) < cutoff:
                continue
            if group_name and not fuzzy_match(p.get("group_name") or "", group_name):
                continue
            out.append(p)
            if len(out) >= max_posts:
                break
        return out

    async def get_post_with_comments(self, post_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        post, comments = await asyncio.gather(
            self._client.get_post(post_id),
            self._client.get_post_comments(post_id),
        )
        return post, comments

    async def get_chats(self, max_chats: int = 10) -> list[dict[str, Any]]:
        return await self._client.get_chats(limit=max_chats)

    async def get_chat_messages(self, chat_id: str, limit: int = 10) -> dict[str, Any]:
        cleaned = (chat_id or "").strip()
        if ":" in cleaned:
            ch_type, ch_id = cleaned.split(":", 1)
        else:
            ch_type, ch_id = "messaging", cleaned
        capped = min(max(limit, 1), 50)
        return await self._client.get_chat_messages(ch_type, ch_id, limit=capped)

    async def get_drive_files(self) -> list[dict[str, Any]]:
        return await self._client.get_drive_files()

    async def get_drive_file(self, file_id: int) -> dict[str, Any]:
        return await self._client.get_drive_file(file_id)


# --- Formatting functions ---


def format_groups(groups: list[dict[str, Any]]) -> str:
    if not groups:
        return "Ingen Hoopit-grupper funnet."
    lines = [f"Hoopit-grupper ({len(groups)}):"]
    for g in groups:
        name = g.get("name", "Ukjent gruppe")
        org = g.get("organization_name") or ""
        div = g.get("division_name") or ""
        members = g.get("members") or []
        member_names = ", ".join(m.get("first_name", "") for m in members if m.get("first_name"))
        meta = " · ".join(p for p in (org, div) if p)
        line = f"- **{name}** (ID: {g.get('id')})"
        if meta:
            line += f" — {meta}"
        if member_names:
            line += f" | Medlemmer: {member_names}"
        lines.append(line)
    return "\n".join(lines)


def format_event_summary(ev: dict[str, Any]) -> str:
    title = ev.get("title", "Ukjent aktivitet")
    etype = format_event_type(ev.get("event_type"))
    dt = parse_timestamp(ev.get("datetime"))
    end_dt = parse_timestamp(ev.get("end"))
    loc = ev.get("location") or ""
    group_name = (ev.get("group") or {}).get("name") or ""
    eid_str = format_event_id(ev.get("event_id", ""), ev.get("id", ""))

    lines = [f"### {title} ({etype})"]
    lines.append(f"- **Tid:** {format_datetime_no(dt, end_dt)}")
    if group_name:
        lines.append(f"- **Gruppe:** {group_name}")
    if loc:
        lines.append(f"- **Sted:** {loc}")

    related = ev.get("related_users") or []
    if related:
        statuses = [
            f"{_event_user_name(ru.get('user'))}: {format_response_status(ru.get('response'))}" for ru in related
        ]
        lines.append(f"- **Status:** {', '.join(statuses)}")

    acc = ev.get("accepted_participant_count")
    dec = ev.get("declined_participant_count")
    un = ev.get("not_responded_participant_count")
    if acc is not None or dec is not None or un is not None:
        lines.append(f"- **Påmelding:** {acc or 0} kommer, {dec or 0} kan ikke, {un or 0} ikke svart")

    lines.append(f"- **ID:** `{eid_str}`")
    return "\n".join(lines)


def format_event_details(detail: dict[str, Any], my_responses: dict[str, Any]) -> str:
    title = detail.get("title", "Ukjent aktivitet")
    etype = format_event_type(detail.get("event_type"))
    dt = parse_timestamp(detail.get("start"))
    end_dt = parse_timestamp(detail.get("end"))
    meetup_dt = parse_timestamp(detail.get("meetup_time"))
    deadline_dt = parse_timestamp(detail.get("response_deadline"))
    eid_str = format_event_id(detail.get("event", ""), detail.get("id", ""))

    lines = [f"## {title} ({etype})"]
    if detail.get("cancelled"):
        reason = detail.get("cancelled_reason") or "Ingen årsak oppgitt"
        lines.append(f"⚠️ **AVLYST:** {reason}")

    lines.append(f"- **Tid:** {format_datetime_no(dt, end_dt)}")
    if meetup_dt:
        lines.append(f"- **Oppmøte:** {meetup_dt.strftime('%H:%M')}")
    if deadline_dt:
        lines.append(f"- **Svarfrist:** {format_datetime_no(deadline_dt)}")

    group_name = (detail.get("group") or {}).get("name")
    if group_name:
        lines.append(f"- **Gruppe:** {group_name}")

    loc_name = (detail.get("location") or {}).get("name") or (detail.get("event_location") or {}).get("name")
    if loc_name:
        lines.append(f"- **Sted:** {loc_name}")

    meetup_loc = (detail.get("meetup_location") or {}).get("name")
    if meetup_loc and meetup_loc != loc_name:
        lines.append(f"- **Oppmøtested:** {meetup_loc}")

    home = detail.get("home_team_name")
    away = detail.get("away_team_name")
    if home and away:
        lines.append(f"- **Kamp:** {home} – {away}")

    invites = detail.get("invites") or {}
    if invites:
        acc = invites.get("participants_accepted", 0)
        dec = invites.get("participants_declined", 0)
        tot = invites.get("participants_total", 0)
        un = detail.get("not_responded_count", max(0, tot - acc - dec))
        lines.append(f"- **Påmelding:** {acc} kommer, {dec} kan ikke, {un} ikke svart (totalt {tot})")

    controllable = [*(my_responses.get("participants") or []), *(my_responses.get("organizers") or [])]
    if controllable:
        lines.append("\n**Dine svar:**")
        for item in controllable:
            name = _event_user_name(item.get("user"))
            st = format_response_status(item.get("response"))
            reason = item.get("response_reason")
            extra = f" ({reason})" if reason else ""
            lines.append(f"- {name}: {st}{extra}")

    desc = (detail.get("description") or "").strip()
    if desc:
        lines.append(f"\n**Beskrivelse:**\n{desc}")

    attachments = detail.get("attachments") or []
    if attachments:
        lines.append(f"\n**Vedlegg ({len(attachments)}):**")
        for att in attachments:
            att_id = att.get("id", "?")
            att_name = att.get("name") or att.get("url") or f"Vedlegg {att_id}"
            lines.append(f"- 📎 {att_name} (ID: {att_id})")

    lines.append(f"\n**ID:** `{eid_str}`")
    return "\n".join(lines)


def format_attendance(events: list[dict[str, Any]], member_name: str | None = None) -> str:
    if not events:
        return "Ingen kommende aktiviteter funnet i perioden."

    unanswered: list[str] = []
    answered: list[str] = []

    for ev in events:
        title = ev.get("title", "Ukjent")
        dt = parse_timestamp(ev.get("datetime"))
        when = format_datetime_no(dt)
        eid_str = format_event_id(ev.get("event_id", ""), ev.get("id", ""))
        group_name = (ev.get("group") or {}).get("name") or ""

        related = ev.get("related_users") or []
        if member_name:
            related = [ru for ru in related if fuzzy_match(_event_user_name(ru.get("user")), member_name)]

        for ru in related:
            uname = _event_user_name(ru.get("user"))
            resp = (ru.get("response") or "not_answered").lower()
            st = format_response_status(resp)
            entry = f"- **{title}** ({group_name}) — {when} | {uname}: {st} | ID: `{eid_str}`"
            if resp in ("not_answered", "not_responded", ""):
                unanswered.append(entry)
            else:
                answered.append(entry)

    lines: list[str] = []
    if unanswered:
        lines.append(f"### ❓ Mangler svar ({len(unanswered)})")
        lines.extend(unanswered)
    else:
        lines.append("### ✅ Alle kommende aktiviteter er besvart!")

    if answered:
        lines.append(f"\n### Besvarte aktiviteter ({len(answered)})")
        lines.extend(answered)

    return "\n".join(lines)


def format_updates(notifications: list[dict[str, Any]]) -> str:
    if not notifications:
        return "Ingen nye oppdateringer i perioden."
    lines = [f"Oppdateringer ({len(notifications)}):"]
    for n in notifications:
        dt = parse_timestamp(n.get("timestamp"))
        when = dt.strftime("%d.%m %H:%M") if dt else "Ukjent tid"
        data = n.get("data") or {}
        title = data.get("title") or "Varsel"
        body = data.get("body") or ""
        group = data.get("EXTRA_GROUP_NAME") or ""
        unread = "🔵 " if not n.get("is_seen") else ""
        g_part = f" [{group}]" if group else ""
        text = f"{title}: {body}" if body else title
        lines.append(f"- {unread}{when}{g_part} — {text}")
    return "\n".join(lines)


def format_posts(posts: list[dict[str, Any]]) -> str:
    if not posts:
        return "Ingen innlegg funnet i perioden."
    lines = [f"Innlegg ({len(posts)}):"]
    for p in posts:
        pid = p.get("id")
        title = (p.get("title") or "").strip()
        text = (p.get("text") or "").strip()
        preview = text[:200] + ("…" if len(text) > 200 else "")
        poster = p.get("poster_name") or "Ukjent"
        group = p.get("group_name") or ""
        dt = parse_timestamp(p.get("created_at"))
        when = dt.strftime("%d.%m.%Y %H:%M") if dt else ""
        comments = p.get("comment_count", 0)
        likes = p.get("likes_count", 0)
        files = p.get("file_count") or len(p.get("attachments") or [])
        header = title or "(Uten tittel)"
        att_str = f" · 📎 {files}" if files else ""
        lines.append(
            f"\n### {header}\n"
            f"- **Gruppe:** {group} · **Av:** {poster} · {when}\n"
            f"- **Aktivitet:** 💬 {comments} kommentarer · 👍 {likes}{att_str} · **ID:** `{pid}`"
        )
        if preview:
            lines.append(f"\n{preview}")
    return "\n".join(lines)


def format_post_comments(post: dict[str, Any], comments: list[dict[str, Any]]) -> str:
    title = (post.get("title") or "").strip() or "(Uten tittel)"
    text = (post.get("text") or "").strip()
    poster = post.get("poster_name") or "Ukjent"
    group = post.get("group_name") or ""
    dt = parse_timestamp(post.get("created_at"))
    when = dt.strftime("%d.%m.%Y %H:%M") if dt else ""

    lines = [
        f"## {title}",
        f"- **Gruppe:** {group} · **Av:** {poster} · {when} · **ID:** `{post.get('id')}`",
    ]
    if text:
        lines.append(f"\n{text}")

    attachments = post.get("attachments") or []
    if attachments:
        lines.append(f"\n**Vedlegg ({len(attachments)}):**")
        for att in attachments:
            lines.append(f"- 📎 Vedlegg {att.get('id', '?')}: {att.get('url', '(ingen lenke)')}")

    lines.append(f"\n### Kommentarer ({len(comments)})")
    if not comments:
        lines.append("Ingen kommentarer ennå.")
    else:
        for c in comments:
            author = c.get("user_name") or _event_user_name(c.get("user")) or c.get("poster_name") or "Ukjent"
            cdt = parse_timestamp(c.get("created_at") or c.get("timestamp"))
            cwhen = cdt.strftime("%d.%m %H:%M") if cdt else ""
            ctext = (c.get("text") or c.get("body") or "").strip()
            lines.append(f"- **{author}** ({cwhen}): {ctext}")
    return "\n".join(lines)


def format_chats(channels: list[dict[str, Any]], current_user_uuid: str | None = None) -> str:
    if not channels:
        return "Ingen chat-samtaler funnet."
    lines = [f"Chat-samtaler ({len(channels)}):"]
    for item in channels:
        ch = item.get("channel") or {}
        cid = ch.get("cid") or f"{ch.get('type', 'messaging')}:{ch.get('id', '')}"
        name = ch.get("name")
        members = item.get("members") or []
        if not name:
            member_names = [(m.get("user") or {}).get("name") or (m.get("user") or {}).get("id") or "" for m in members]
            name = ", ".join(n for n in member_names if n) or "Privat samtale"
        msgs = item.get("messages") or []
        last_msg = msgs[-1] if msgs else None
        last_text = ""
        if last_msg:
            sender = (last_msg.get("user") or {}).get("name") or "Ukjent"
            body = (last_msg.get("text") or "").strip()
            if not body and last_msg.get("attachments"):
                body = f"[📎 {len(last_msg['attachments'])} vedlegg]"
            last_text = f" — {sender}: {body[:100]}"
        dt = parse_timestamp(ch.get("last_message_at"))
        when = dt.strftime("%d.%m %H:%M") if dt else ""
        reads = item.get("read") or []
        if current_user_uuid:
            unread = sum(
                r.get("unread_messages", 0)
                for r in reads
                if r.get("unread_messages") and (r.get("user") or {}).get("id") == current_user_uuid
            )
        else:
            unread = sum(r.get("unread_messages", 0) for r in reads if r.get("unread_messages"))
        unread_tag = f" 🔵 ({unread} uleste)" if unread else ""
        lines.append(f"- **{name}** ({len(members)} deltakere, {when}){unread_tag}{last_text}\n  ID: `{cid}`")
    return "\n".join(lines)


def format_chat_messages(channel_data: dict[str, Any]) -> str:
    ch = channel_data.get("channel") or {}
    name = ch.get("name") or ch.get("cid") or "Chat"
    messages = channel_data.get("messages") or []
    if not messages:
        return f"Ingen meldinger i **{name}**."
    lines = [f"## Chat: {name} ({len(messages)} meldinger)"]
    for m in messages:
        sender = (m.get("user") or {}).get("name") or "Ukjent"
        dt = parse_timestamp(m.get("created_at"))
        when = dt.strftime("%d.%m %H:%M") if dt else ""
        text = (m.get("text") or "").strip()
        atts = m.get("attachments") or []
        att_str = ""
        if atts:
            urls = [a.get("asset_url") or a.get("image_url") or a.get("title") or "vedlegg" for a in atts]
            att_str = " " + " ".join(f"[📎 {u}]" for u in urls)
        lines.append(f"- **{sender}** ({when}): {text}{att_str}")
    return "\n".join(lines)


def format_drive_files(files: list[dict[str, Any]]) -> str:
    if not files:
        return "Ingen filer funnet i Hoopit Drive."
    lines = [f"Filer i Hoopit Drive ({len(files)}):"]
    for f in files:
        fid = f.get("id")
        name = f.get("name", "Ukjent fil")
        ftype = f.get("file_type") or f.get("mime_type") or "fil"
        size = format_size(f.get("size"))
        gpath = " / ".join(g.get("name", "") for g in (f.get("group_path") or []) if g.get("name"))
        dt = parse_timestamp(f.get("last_modified"))
        when = dt.strftime("%d.%m.%Y") if dt else ""
        lines.append(f"- 📎 **{name}** ({ftype}, {size}) — {gpath} · {when} · ID: `{fid}`")
    return "\n".join(lines)


def format_drive_file_detail(f: dict[str, Any]) -> str:
    name = f.get("name", "Ukjent fil")
    ftype = f.get("file_type") or f.get("mime_type") or "fil"
    size = format_size(f.get("size"))
    group = (f.get("group") or {}).get("name") or ""
    author = _event_user_name(f.get("last_modified_by"))
    dt = parse_timestamp(f.get("last_modified_at") or f.get("updated_at"))
    when = format_datetime_no(dt) if dt else "Ukjent"
    lines = [
        f"## 📎 {name}",
        f"- **ID:** `{f.get('id')}`",
        f"- **Type:** {ftype} ({f.get('mime_type', '')})",
        f"- **Størrelse:** {size}",
        f"- **Gruppe:** {group}",
        f"- **Sist endret:** {when} av {author}",
    ]
    if f.get("url") or f.get("download_url"):
        lines.append(f"- **Lenke:** {f.get('url') or f.get('download_url')}")
    return "\n".join(lines)


# --- MCP Tool Definitions ---

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_groups",
        "description": "List alle Hoopit-grupper med klubb, avdeling og medlemmer.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_upcoming_events",
        "description": (
            "Hent kommende aktiviteter fra Hoopit. Kan filtreres på gruppe eller medlem/barn. Standard: 7 dager frem."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Antall dager å inkludere (standard: 7). Vindu = [from_days, from_days+days].",
                    "default": 7,
                },
                "from_days": {
                    "type": "integer",
                    "description": "Start fra N dager frem i tid (standard: 0 = fra nå).",
                    "default": 0,
                },
                "group_name": {
                    "type": "string",
                    "description": "Filtrér på gruppenavn (delvis treff, f.eks. 'Jenter 2016').",
                },
                "member_name": {
                    "type": "string",
                    "description": "Filtrér på navn på barn/medlem (delvis treff).",
                },
            },
        },
    },
    {
        "name": "get_event_details",
        "description": "Hent detaljer for en spesifikk aktivitet, inkludert beskrivelse, sted, oppmøte og svarstatus.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Aktivitetens ID på formen '<event_id>:<occurrence_id>' (fra get_upcoming_events).",
                },
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "get_attendance",
        "description": "Vis påmeldingsstatus for kommende aktiviteter. Fremhever hvilke aktiviteter som mangler svar.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": "Navn på barn/medlem (valgfritt — viser alle hvis ikke satt).",
                },
                "days": {
                    "type": "integer",
                    "description": "Antall dager å inkludere (standard: 14).",
                    "default": 14,
                },
                "from_days": {
                    "type": "integer",
                    "description": "Start fra N dager frem i tid (standard: 0 = fra nå).",
                    "default": 0,
                },
            },
        },
    },
    {
        "name": "respond_to_event",
        "description": "Svar på en aktivitet — aksepter eller avslå for deg selv eller et barn.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Aktivitetens ID på formen '<event_id>:<occurrence_id>'.",
                },
                "accept": {
                    "type": "boolean",
                    "description": "true = kommer, false = kan ikke.",
                },
                "member_name": {
                    "type": "string",
                    "description": "Navn på barn/medlem (valgfritt dersom bare én person kan besvares).",
                },
                "decline_message": {
                    "type": "string",
                    "description": "Årsak/melding ved avslag (valgfritt).",
                },
            },
            "required": ["event_id", "accept"],
        },
    },
    {
        "name": "search_events",
        "description": "Søk i kommende aktiviteter etter tekst i tittel, sted, type eller gruppenavn.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Søketekst (f.eks. 'cup', 'kamp', 'trening', 'avslutning').",
                },
                "days": {
                    "type": "integer",
                    "description": "Antall dager å søke i (standard: 30).",
                    "default": 30,
                },
                "from_days": {
                    "type": "integer",
                    "description": "Start fra N dager frem i tid (standard: 0 = fra nå).",
                    "default": 0,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_updates",
        "description": (
            "Hva er nytt i Hoopit (varslingsfanen): nye innlegg, kommentarer, invitasjoner og endringer, "
            "nyeste først. Markerer ingenting som lest."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_name": {
                    "type": "string",
                    "description": "Filtrér på gruppenavn (delvis treff).",
                },
                "since_hours": {
                    "type": "integer",
                    "description": "Bare hendelser fra de siste N timene (standard: 48).",
                    "default": 48,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maks antall hendelser (standard: 20).",
                    "default": 20,
                },
            },
        },
    },
    {
        "name": "get_posts",
        "description": (
            "Hent innlegg fra gruppeveggen i Hoopit (beskjeder fra trenere/lagledere), med antall kommentarer "
            "og vedlegg. Les kommentarene med get_post_comments."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_name": {
                    "type": "string",
                    "description": "Filtrér på gruppenavn (delvis treff). Viser alle grupper hvis tom.",
                },
                "max_posts": {
                    "type": "integer",
                    "description": "Maks antall innlegg å hente (standard: 10).",
                    "default": 10,
                },
                "since_days": {
                    "type": "integer",
                    "description": "Hopp over innlegg eldre enn så mange dager (standard: 14).",
                    "default": 14,
                },
            },
        },
    },
    {
        "name": "get_post_comments",
        "description": "Hent et innlegg med full tekst, vedlegg og alle kommentarer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_id": {
                    "type": "integer",
                    "description": "Innleggets ID (fra get_posts).",
                },
            },
            "required": ["post_id"],
        },
    },
    {
        "name": "get_chats",
        "description": (
            "Hent nylige chat-samtaler i Hoopit (privat, gruppe og foresatte) med siste melding. "
            "Les en samtale med get_chat_messages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_chats": {
                    "type": "integer",
                    "description": "Maks antall samtaler å hente (standard: 10).",
                    "default": 10,
                },
            },
        },
    },
    {
        "name": "get_chat_messages",
        "description": "Hent meldingshistorikken i en chat-samtale, eldste først. Markerer ikke som lest.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "Samtalens ID (f.eks. 'messaging:<uuid>' fra get_chats).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maks antall meldinger å hente (standard: 10, maks: 50).",
                    "default": 10,
                },
            },
            "required": ["chat_id"],
        },
    },
    {
        "name": "get_attachment",
        "description": (
            "Hent vedlegg eller filer fra Hoopit. Støtter source='drive' (lister Drive-filer hvis item_id utelates, "
            "eller henter detaljer for én fil), source='post' (vedleggs-lenker for et innlegg), eller "
            "source='event' (vedlegg for en aktivitet)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["drive", "post", "event"],
                    "description": "Kilde: 'drive' (standard), 'post', eller 'event'.",
                    "default": "drive",
                },
                "item_id": {
                    "type": "string",
                    "description": (
                        "Fil-ID (for drive), post-ID (for post), eller '<event_id>:<occurrence_id>' (for event)."
                    ),
                },
            },
        },
    },
]


async def handle_tool_call(service: HoopitService, name: str, arguments: dict[str, Any] | None) -> str:
    args = arguments or {}

    if name == "list_groups":
        groups = await service.get_groups()
        return format_groups(groups)

    if name == "get_upcoming_events":
        days = int(args.get("days", 7))
        from_days = int(args.get("from_days", 0))
        events = await service.get_upcoming_events(
            days=days,
            from_days=from_days,
            group_name=args.get("group_name"),
            member_name=args.get("member_name"),
        )
        if not events:
            return f"Ingen kommende aktiviteter funnet de neste {days} dagene."
        header = f"Kommende aktiviteter ({len(events)}):"
        return header + "\n\n" + "\n\n".join(format_event_summary(e) for e in events)

    if name == "get_event_details":
        event_id = str(args.get("event_id", ""))
        detail, my_responses = await service.get_event_details(event_id)
        return format_event_details(detail, my_responses)

    if name == "get_attendance":
        days = int(args.get("days", 14))
        from_days = int(args.get("from_days", 0))
        member_name = args.get("member_name")
        events = await service.get_upcoming_events(days=days, from_days=from_days, member_name=member_name)
        return format_attendance(events, member_name=member_name)

    if name == "respond_to_event":
        event_id = str(args.get("event_id", ""))
        accept = bool(args.get("accept"))
        member_name = args.get("member_name")
        decline_message = str(args.get("decline_message") or "")
        resolved_name, result = await service.respond_to_event(
            event_id,
            accept=accept,
            member_name=member_name,
            decline_message=decline_message,
        )
        expected = RESPONSE_ACCEPTED if accept else RESPONSE_DECLINED
        actual = result.get("response", expected) if isinstance(result, dict) else expected
        status_label = format_response_status(actual)
        msg = f"Svar registrert for **{resolved_name}** på aktivitet `{event_id}`: {status_label}"
        if not accept and decline_message:
            msg += f" (årsak: {decline_message})"
        return msg

    if name == "search_events":
        query = str(args.get("query", "")).strip()
        if not query:
            return "Oppgi en søketekst i `query`."
        days = int(args.get("days", 30))
        from_days = int(args.get("from_days", 0))
        events = await service.get_upcoming_events(days=days, from_days=from_days)
        q_lower = query.lower()
        matches = [
            e
            for e in events
            if q_lower in (e.get("title") or "").lower()
            or q_lower in (e.get("location") or "").lower()
            or q_lower in (e.get("event_type") or "").lower()
            or q_lower in ((e.get("group") or {}).get("name") or "").lower()
        ]
        if not matches:
            return f"Ingen aktiviteter matchet '{query}' i løpet av de neste {days} dagene."
        header = f"Søketreff for '{query}' ({len(matches)}):"
        return header + "\n\n" + "\n\n".join(format_event_summary(e) for e in matches)

    if name == "get_updates":
        updates = await service.get_updates(
            group_name=args.get("group_name"),
            since_hours=int(args.get("since_hours", 48)),
            limit=int(args.get("limit", 20)),
        )
        return format_updates(updates)

    if name == "get_posts":
        posts = await service.get_posts(
            group_name=args.get("group_name"),
            max_posts=int(args.get("max_posts", 10)),
            since_days=int(args.get("since_days", 14)),
        )
        return format_posts(posts)

    if name == "get_post_comments":
        post_id = int(args["post_id"])
        post, comments = await service.get_post_with_comments(post_id)
        return format_post_comments(post, comments)

    if name == "get_chats":
        chats = await service.get_chats(max_chats=int(args.get("max_chats", 10)))
        return format_chats(chats, current_user_uuid=service.user_uuid)

    if name == "get_chat_messages":
        chat_id = str(args.get("chat_id", ""))
        limit = int(args.get("limit", 10))
        ch_data = await service.get_chat_messages(chat_id, limit=limit)
        return format_chat_messages(ch_data)

    if name == "get_attachment":
        source = str(args.get("source") or "drive").lower()
        item_id = args.get("item_id")
        if source == "drive":
            if not item_id:
                files = await service.get_drive_files()
                return format_drive_files(files)
            detail = await service.get_drive_file(int(item_id))
            return format_drive_file_detail(detail)
        if source == "post":
            if not item_id:
                return "Oppgi `item_id` (post-ID) for å hente vedlegg fra et innlegg."
            post, _ = await service.get_post_with_comments(int(item_id))
            atts = post.get("attachments") or []
            if not atts:
                return f"Innlegg `{item_id}` har ingen vedlegg."
            lines = [f"Vedlegg for innlegg `{item_id}` ({len(atts)}):"]
            for att in atts:
                lines.append(f"- 📎 ID `{att.get('id', '?')}`: {att.get('url', '(ingen lenke)')}")
            return "\n".join(lines)
        if source == "event":
            if not item_id:
                return "Oppgi `item_id` ('<event_id>:<occurrence_id>') for å hente vedlegg fra en aktivitet."
            detail, _ = await service.get_event_details(str(item_id))
            atts = detail.get("attachments") or []
            if not atts:
                return f"Aktivitet `{item_id}` har ingen vedlegg."
            lines = [f"Vedlegg for aktivitet `{item_id}` ({len(atts)}):"]
            for att in atts:
                name_or_url = att.get("name") or att.get("url") or f"Vedlegg {att.get('id', '?')}"
                url = att.get("url")
                extra = f": {url}" if url and url != name_or_url else ""
                lines.append(f"- 📎 {name_or_url}{extra}")
            return "\n".join(lines)
        return f"Ukjent vedleggskilde: '{source}'. Bruk 'drive', 'post', eller 'event'."

    return f"Ukjent verktøy: {name}"
