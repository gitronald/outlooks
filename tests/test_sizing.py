"""Tests for window sizes and the item-id check."""

import json

from outlooks import capture as cp
from outlooks import sizing as sz
from outlooks.coverage import parse_ts

BOX = "box@example.org"
SEARCH = "mcp__claude_ai_Microsoft_365__outlook_email_search"


def hit(n, offset=0, item_id=None, mid=None):
    return {
        "uri": f"mail:///messages/ID{n}?owner=box%40example.org",
        "id": item_id or f"ID{n}",
        "subject": f"message {n}",
        "sender": "someone@example.com",
        "recipients": [BOX],
        "receivedDateTime": "2026-03-10T12:00:00.000Z",
        "summary": "text",
        "internetMessageId": mid or f"<m{n}@example.com>",
        "isRead": True,
        "offset": offset,
    }


def trailer(total, next_offset=None):
    return {
        "moreResults": next_offset is not None,
        "nextOffset": next_offset,
        "totalResultCount": total,
    }


def blocks(*items):
    return [
        {"type": "text", "text": i if isinstance(i, str) else json.dumps(i)}
        for i in items
    ]


def write(folder, stamp, args, *items, mailbox=BOX):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stamp}-1.json"
    path.write_text(
        json.dumps(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": SEARCH,
                "tool_input": {"mailboxOwnerEmail": mailbox, **args},
                "tool_response": blocks(*items),
            }
        ),
        encoding="utf-8",
    )
    return path


def window(after, before=None, offset=0, extra=None):
    args = {"afterDateTime": after, "limit": 2, "offset": offset, **(extra or {})}
    if before is not None:
        args["beforeDateTime"] = before
    return args


def load(folder):
    captures, _ = cp.load_captures([folder])
    return captures


# --- window_sizes -----------------------------------------------------------------


def test_window_sizes_takes_the_newest_capture_of_the_same_bounds(tmp_path):
    write(
        tmp_path,
        "20260913T1",
        window("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"),
        hit(1),
        trailer(1),
    )
    write(
        tmp_path,
        "20260913T2",
        window("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"),
        hit(1),
        hit(2),
        trailer(2),
    )
    sizes = sz.window_sizes(load(tmp_path), BOX)
    assert (
        sizes[(parse_ts("2026-03-01T00:00:00Z"), parse_ts("2026-04-01T00:00:00Z"))] == 2
    )


def test_window_sizes_gives_zero_for_an_empty_offset_zero_capture(tmp_path):
    write(
        tmp_path, "20260913T1", window("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z")
    )
    sizes = sz.window_sizes(load(tmp_path), BOX)
    assert (
        sizes[(parse_ts("2026-03-01T00:00:00Z"), parse_ts("2026-04-01T00:00:00Z"))] == 0
    )


def test_window_sizes_ignores_other_mailboxes_and_lookups(tmp_path):
    write(
        tmp_path,
        "20260913T1",
        window("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"),
        hit(1),
        trailer(1),
        mailbox="other@example.com",
    )
    write(
        tmp_path,
        "20260913T2",
        window(
            "2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z", extra={"query": "inquiry"}
        ),
        hit(2),
        trailer(1),
    )
    sizes = sz.window_sizes(load(tmp_path), BOX)
    assert sizes == {}


def test_window_sizes_keys_an_open_ended_capture_on_before_none(tmp_path):
    write(tmp_path, "20260913T1", window("2026-03-01T00:00:00Z"), hit(1), trailer(1))
    sizes = sz.window_sizes(load(tmp_path), BOX)
    assert sizes == {(parse_ts("2026-03-01T00:00:00Z"), None): 1}


def test_window_sizes_ignores_a_page_past_the_end(tmp_path):
    write(
        tmp_path,
        "20260913T1",
        window("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"),
        hit(1),
        trailer(1),
    )
    write(
        tmp_path,
        "20260913T2",
        window("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z", offset=25),
        {"totalResultCount": 25},
    )
    sizes = sz.window_sizes(load(tmp_path), BOX)
    assert (
        sizes[(parse_ts("2026-03-01T00:00:00Z"), parse_ts("2026-04-01T00:00:00Z"))] == 1
    )


# --- id_check ---------------------------------------------------------------------
#
# id_check's baseline lookup and its counting loop share one rule ("bounded and
# within the range"), so a whole-range probe whose own bounds equal (after, before)
# is itself counted as one of the windows, not just a number read off to the side.


def test_id_check_ok_with_a_shared_boundary_message(tmp_path):
    after, before = "2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z"
    mid = "2026-02-01T00:00:00Z"
    # The whole-range probe (baseline 3) returns item 3, the newest message, which
    # a later page also lists: sharing an id does not inflate the id set.
    write(
        tmp_path,
        "20260913T1",
        window(after, before, extra={"limit": 1}),
        hit(3, item_id="I3"),
        trailer(3),
    )
    # Item 2 sits on the boundary between the two windows: bounds are inclusive, so
    # it is listed by both, with the same id both times.
    write(
        tmp_path,
        "20260913T2",
        window(after, mid),
        hit(1, item_id="I1"),
        hit(2, item_id="I2"),
        trailer(2),
    )
    write(
        tmp_path,
        "20260913T3",
        window(mid, before),
        hit(2, item_id="I2"),
        hit(3, item_id="I3"),
        trailer(2),
    )
    captures = load(tmp_path)
    check = sz.id_check(captures, BOX, parse_ts(after), parse_ts(before))
    assert check.baseline == 3
    assert check.ids == 3  # I1, I2 (boundary, deduped), I3
    assert check.windows == 3  # the probe counts as a window capture too
    assert check.ok


def test_id_check_folder_copies_give_fewer_messages_than_ids(tmp_path):
    after, before = "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"
    write(
        tmp_path,
        "20260913T1",
        window(after, before),
        hit(1, item_id="A1", mid="<m1@example.com>"),
        hit(1, item_id="A1b", mid="<m1@example.com>"),  # a folder copy: same message
        trailer(2),
    )
    captures = load(tmp_path)
    check = sz.id_check(captures, BOX, parse_ts(after), parse_ts(before))
    assert check.ids == 2 and check.messages == 1
    assert check.messages < check.ids


def test_id_check_missing_baseline_is_not_ok(tmp_path):
    # Two sub-windows tile the range, but no capture ever probed it as a whole.
    after, before = "2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z"
    mid = "2026-02-01T00:00:00Z"
    write(tmp_path, "20260913T1", window(after, mid), hit(1, item_id="I1"), trailer(1))
    write(tmp_path, "20260913T2", window(mid, before), hit(2, item_id="I2"), trailer(1))
    captures = load(tmp_path)
    check = sz.id_check(captures, BOX, parse_ts(after), parse_ts(before))
    assert check.baseline is None
    assert check.windows == 2 and check.ids == 2
    assert not check.ok


def test_id_check_excludes_windows_outside_the_range_and_open_ended(tmp_path):
    after, before = "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"
    write(
        tmp_path, "20260913T1", window(after, before), hit(1, item_id="I1"), trailer(1)
    )
    # A window reaching past `before` and an open-ended one: neither counted.
    write(
        tmp_path,
        "20260913T2",
        window(after, "2026-03-01T00:00:00Z"),
        hit(2, item_id="I2"),
        trailer(1),
    )
    write(tmp_path, "20260913T3", window(after), hit(3, item_id="I3"), trailer(1))
    captures = load(tmp_path)
    check = sz.id_check(captures, BOX, parse_ts(after), parse_ts(before))
    assert check.windows == 1 and check.ids == 1
    assert check.baseline == 1
    assert check.ok


def test_id_check_with_no_captures_is_not_ok():
    after, before = (
        parse_ts("2026-01-01T00:00:00Z"),
        parse_ts("2026-02-01T00:00:00Z"),
    )
    check = sz.id_check([], BOX, after, before)
    assert check.baseline is None and check.windows == 0 and check.ids == 0
    assert not check.ok
