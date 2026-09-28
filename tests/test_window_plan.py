"""Tests for the mailbox window backfill planner.

Pure-function tests on synthetic state: ``sizes`` dicts and coverage row dicts
are built directly here, no captures needed.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from outlooks import coverage as cv
from outlooks import window_plan as wp

MAILBOX = "box@example.org"
PACIFIC = ZoneInfo("America/Los_Angeles")  # the zone [tool.outlooks] sets here


def row(mailbox: str, after: datetime, before: datetime) -> dict[str, str]:
    return {"mailbox": mailbox, "after": cv.fmt_ts(after), "before": cv.fmt_ts(before)}


# --- parse_bound ------------------------------------------------------------


def test_parse_bound_winter_date_is_pacific_midnight_as_utc():
    assert wp.parse_bound("2026-01-15") == datetime(2026, 1, 15, 8, 0, tzinfo=UTC)


def test_parse_bound_summer_date_is_pacific_midnight_as_utc():
    assert wp.parse_bound("2026-07-15") == datetime(2026, 7, 15, 7, 0, tzinfo=UTC)


def test_parse_bound_accepts_a_z_timestamp():
    assert wp.parse_bound("2026-03-10T12:00:00Z") == datetime(
        2026, 3, 10, 12, 0, tzinfo=UTC
    )


def test_parse_bound_rejects_a_naive_timestamp():
    with pytest.raises(wp.PlanError, match="naive"):
        wp.parse_bound("2026-03-10T12:00:00")


def test_parse_bound_rejects_a_fraction_of_a_second():
    # The state file holds whole seconds, so a fraction would not survive a save.
    with pytest.raises(wp.PlanError, match="fraction"):
        wp.parse_bound("2026-03-10T12:00:00.500000Z")


def test_a_plan_survives_a_save_and_load(tmp_path):
    plan = wp.init(
        MAILBOX, wp.parse_bound("2026-01-15"), wp.parse_bound("2026-03-10T12:00:01Z")
    )
    plan.pieces[0].total = 12
    wp.save(plan, tmp_path)
    assert wp.load(tmp_path) == plan


# --- init ---------------------------------------------------------------------


def test_init_builds_contiguous_month_pieces_with_exact_endpoints():
    after = wp.parse_bound("2026-01-15")
    before = wp.parse_bound("2026-04-10")
    plan = wp.init(MAILBOX, after, before)

    assert plan.pieces[0].after == after
    assert plan.pieces[-1].before == before
    for piece, nxt in zip(plan.pieces, plan.pieces[1:]):
        assert piece.before == nxt.after
    assert all(piece.total is None for piece in plan.pieces)
    assert [piece.after for piece in plan.pieces[1:]] == [
        datetime(2026, 2, 1, tzinfo=PACIFIC).astimezone(UTC),
        datetime(2026, 3, 1, tzinfo=PACIFIC).astimezone(UTC),
        datetime(2026, 4, 1, tzinfo=PACIFIC).astimezone(UTC),
    ]


def test_init_spans_the_march_and_november_dst_changes():
    after = wp.parse_bound("2026-01-01")
    before = wp.parse_bound("2026-12-01")
    plan = wp.init(MAILBOX, after, before)

    starts = {piece.after for piece in plan.pieces}
    march = datetime(2026, 3, 1, tzinfo=PACIFIC).astimezone(UTC)
    april = datetime(2026, 4, 1, tzinfo=PACIFIC).astimezone(UTC)
    november = datetime(2026, 11, 1, tzinfo=PACIFIC).astimezone(UTC)
    assert {march, april, november} <= starts
    assert march.hour == 8  # PST, before the spring-forward
    assert april.hour == 7  # PDT, after it
    assert november.hour == 7  # still PDT at midnight on fallback day


def test_init_sub_month_range_is_one_piece():
    after = wp.parse_bound("2026-03-10")
    before = wp.parse_bound("2026-03-20")
    plan = wp.init(MAILBOX, after, before)
    assert plan.pieces == [wp.Piece(after, before, None)]


def test_init_rejects_reversed_or_empty_range():
    a = wp.parse_bound("2026-03-01")
    b = wp.parse_bound("2026-03-02")
    with pytest.raises(wp.PlanError):
        wp.init(MAILBOX, a, a)
    with pytest.raises(wp.PlanError):
        wp.init(MAILBOX, b, a)


# --- fill -----------------------------------------------------------------------


def test_fill_leaves_an_absent_piece_as_none():
    plan = wp.init(MAILBOX, wp.parse_bound("2026-01-01"), wp.parse_bound("2026-03-01"))
    wp.fill(plan, {})
    assert all(piece.total is None for piece in plan.pieces)


def test_fill_takes_zero_for_an_empty_window():
    plan = wp.init(MAILBOX, wp.parse_bound("2026-01-01"), wp.parse_bound("2026-02-01"))
    piece = plan.pieces[0]
    wp.fill(plan, {(piece.after, piece.before): 0})
    assert plan.pieces[0].total == 0


def test_fill_lets_a_newer_size_replace_an_older_one():
    plan = wp.init(MAILBOX, wp.parse_bound("2026-01-01"), wp.parse_bound("2026-02-01"))
    piece = plan.pieces[0]
    key = (piece.after, piece.before)
    wp.fill(plan, {key: 50})
    assert plan.pieces[0].total == 50
    wp.fill(plan, {key: 60})
    assert plan.pieces[0].total == 60


def test_fill_sets_the_plan_total_from_a_probe_of_the_whole_range():
    plan = wp.init(MAILBOX, wp.parse_bound("2026-01-01"), wp.parse_bound("2026-02-01"))
    wp.fill(plan, {(plan.after, plan.before): 12345})
    assert plan.total == 12345


# --- split_piece ----------------------------------------------------------------


def test_split_piece_day_split_across_a_dst_change():
    after = wp.parse_bound("2026-03-07")
    before = wp.parse_bound("2026-03-10")
    piece = wp.Piece(after, before, 310)  # k = ceil(310/150) = 3, so 2 cuts wanted
    children = wp.split_piece(piece)

    march8 = datetime(2026, 3, 8, tzinfo=PACIFIC).astimezone(UTC)
    march9 = datetime(2026, 3, 9, tzinfo=PACIFIC).astimezone(UTC)
    assert march8.hour == 8 and march9.hour == 7  # the offset actually changes
    assert [(c.after, c.before) for c in children] == [
        (after, march8),
        (march8, march9),
        (march9, before),
    ]
    assert all(c.total is None for c in children)


def test_split_piece_keeps_non_midnight_endpoints_exactly():
    after = datetime(2026, 6, 10, 15, 30, tzinfo=UTC)
    before = datetime(2026, 6, 13, 15, 30, tzinfo=UTC)
    piece = wp.Piece(after, before, 200)  # k = ceil(200/150) = 2, one cut wanted
    children = wp.split_piece(piece)

    assert children[0].after == after
    assert children[-1].before == before
    june12 = datetime(2026, 6, 12, tzinfo=PACIFIC).astimezone(UTC)
    assert [c.after for c in children[1:]] == [june12]
    assert [c.before for c in children[:-1]] == [june12]


def test_split_piece_same_day_is_second_aligned():
    after = wp.parse_bound("2026-03-10")  # Pacific midnight, winter
    before = after + timedelta(hours=12)  # still the same Pacific day
    piece = wp.Piece(after, before, 220)  # k = ceil(220/110) = 2
    children = wp.split_piece(piece)

    assert len(children) == 2
    assert children[0].after == after and children[-1].before == before
    assert children[0].before == children[1].after == after + timedelta(hours=6)
    assert all(c.total is None for c in children)


def test_split_piece_children_are_contiguous_and_cover_the_parent():
    after = wp.parse_bound("2026-05-01")
    before = wp.parse_bound("2026-06-01")
    piece = wp.Piece(after, before, 400)
    children = wp.split_piece(piece)

    assert children[0].after == after
    assert children[-1].before == before
    for a, b in zip(children, children[1:]):
        assert a.before == b.after


def test_split_piece_a_one_second_piece_is_returned_unchanged():
    start = wp.parse_bound("2026-03-10")
    piece = wp.Piece(start, start + timedelta(seconds=1), 200)
    result = wp.split_piece(piece)
    assert result == [piece]
    assert result[0] is piece


# --- split ------------------------------------------------------------------------


def test_split_replaces_oversized_pieces_and_reports_unsplittable():
    unsized = wp.Piece(wp.parse_bound("2026-01-01"), wp.parse_bound("2026-01-02"))
    small = wp.Piece(wp.parse_bound("2026-01-02"), wp.parse_bound("2026-01-03"), 50)
    tiny_start = wp.parse_bound("2026-01-03")
    tiny = wp.Piece(tiny_start, tiny_start + timedelta(seconds=1), 300)
    big = wp.Piece(wp.parse_bound("2026-02-01"), wp.parse_bound("2026-03-01"), 300)
    plan = wp.Plan(
        MAILBOX, unsized.after, big.before, None, [unsized, small, tiny, big]
    )

    new, unsplittable = wp.split(plan)

    assert unsplittable == [tiny]
    assert new and all(piece.total is None for piece in new)
    assert plan.pieces[:3] == [unsized, small, tiny]
    assert big not in plan.pieces
    assert plan.pieces[3:] == new
    assert new[0].after == big.after and new[-1].before == big.before


# --- remaining --------------------------------------------------------------------


def test_remaining_containment_across_two_adjacent_rows():
    a = wp.parse_bound("2026-01-01")
    mid = wp.parse_bound("2026-01-15")
    b = wp.parse_bound("2026-02-01")
    piece = wp.Piece(a, b, 10)
    plan = wp.Plan(MAILBOX, a, b, None, [piece])
    rows = [row(MAILBOX, a, mid), row(MAILBOX, mid, b)]
    assert wp.remaining(plan, rows) == []


def test_remaining_ignores_another_mailboxs_rows():
    a = wp.parse_bound("2026-01-01")
    b = wp.parse_bound("2026-02-01")
    piece = wp.Piece(a, b, 10)
    plan = wp.Plan(MAILBOX, a, b, None, [piece])
    rows = [row("someone-else@example.com", a, b)]
    assert wp.remaining(plan, rows) == [piece]


def test_remaining_lists_an_uncovered_piece():
    a = wp.parse_bound("2026-01-01")
    b = wp.parse_bound("2026-02-01")
    piece = wp.Piece(a, b, 10)
    plan = wp.Plan(MAILBOX, a, b, None, [piece])
    assert wp.remaining(plan, []) == [piece]


# --- batches ------------------------------------------------------------------------


def _chain(sizes: list[int]) -> list[wp.Piece]:
    pieces = []
    start = wp.parse_bound("2026-01-01")
    for n in sizes:
        end = start + timedelta(days=1)
        pieces.append(wp.Piece(start, end, n))
        start = end
    return pieces


def test_pages_rounds_a_partial_page_up():
    assert [wp.pages(p) for p in _chain([2, 25, 26, 175])] == [1, 1, 2, 7]


def test_batches_packs_pieces_up_to_max_pages():
    pieces = _chain([50, 50, 50])  # ceil(50/25) = 2 pages each, 6 total <= 7
    plan = wp.Plan(MAILBOX, pieces[0].after, pieces[-1].before, None, pieces)
    grouped = wp.batches(plan, [])
    assert grouped == [pieces]


def test_batches_starts_a_new_batch_over_max_pages():
    pieces = _chain([100, 100])  # ceil(100/25) = 4 pages each, 8 > 7
    plan = wp.Plan(MAILBOX, pieces[0].after, pieces[-1].before, None, pieces)
    grouped = wp.batches(plan, [])
    assert grouped == [[pieces[0]], [pieces[1]]]


def test_batches_skips_zero_and_one_message_pieces():
    pieces = _chain([0, 1, 30])
    plan = wp.Plan(MAILBOX, pieces[0].after, pieces[-1].before, None, pieces)
    assert wp.batches(plan, []) == [[pieces[2]]]


def test_batches_skips_a_covered_piece():
    a = wp.parse_bound("2026-01-01")
    b = a + timedelta(days=1)
    piece = wp.Piece(a, b, 30)
    plan = wp.Plan(MAILBOX, a, b, None, [piece])
    assert wp.batches(plan, [row(MAILBOX, a, b)]) == []


def test_batches_raises_for_an_unsized_piece():
    a = wp.parse_bound("2026-01-01")
    b = a + timedelta(days=1)
    plan = wp.Plan(MAILBOX, a, b, None, [wp.Piece(a, b, None)])
    with pytest.raises(wp.PlanError):
        wp.batches(plan, [])


def test_batches_raises_for_an_over_limit_piece():
    a = wp.parse_bound("2026-01-01")
    b = a + timedelta(days=1)
    plan = wp.Plan(MAILBOX, a, b, None, [wp.Piece(a, b, wp.LIMIT + 1)])
    with pytest.raises(wp.PlanError):
        wp.batches(plan, [])


# --- write_batches ------------------------------------------------------------------


def test_write_batches_removes_stale_pager_files(tmp_path):
    (tmp_path / "pager-01.txt").write_text("stale\n", encoding="utf-8")
    (tmp_path / "pager-02.txt").write_text("stale\n", encoding="utf-8")
    a = wp.parse_bound("2026-01-01")
    b = a + timedelta(days=1)
    piece = wp.Piece(a, b, 30)

    written = wp.write_batches([[piece]], tmp_path)

    assert written == [tmp_path / "pager-01.txt"]
    assert not (tmp_path / "pager-02.txt").exists()
    assert (tmp_path / "pager-01.txt").read_text(encoding="utf-8") == (
        f"{cv.fmt_ts(a)} {cv.fmt_ts(b)} 30\n"
    )


# --- save / load --------------------------------------------------------------------


def test_save_and_load_round_trip(tmp_path):
    a = wp.parse_bound("2026-01-01")
    mid = wp.parse_bound("2026-01-15")
    b = wp.parse_bound("2026-02-01")
    plan = wp.Plan(MAILBOX, a, b, 10, [wp.Piece(a, mid, 10), wp.Piece(mid, b, None)])

    wp.save(plan, tmp_path)
    loaded = wp.load(tmp_path)

    assert loaded == plan


@pytest.mark.parametrize("value", ["2025-9-1", "2025-13-01", "2025-02-30", "soon"])
def test_parse_bound_rejects_a_malformed_bound(value):
    with pytest.raises(wp.PlanError, match="not a YYYY-MM-DD date or a timestamp"):
        wp.parse_bound(value)


def test_load_refuses_a_folder_with_no_plan(tmp_path):
    with pytest.raises(wp.PlanError, match="start one with `windows init`"):
        wp.load(tmp_path)
