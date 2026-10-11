"""The ``sweep_cadence`` setting, the due rule, and ``outlooks due``."""

import csv
from datetime import UTC, date, datetime, timedelta

import pytest
from typer.testing import CliRunner

from outlooks import cadence, config
from outlooks import coverage as cv
from outlooks.cadence import Step
from outlooks.cli import app
from outlooks.timestamps import fmt_ts

runner = CliRunner()
BOX = "desk@example.org"
DAY = timedelta(days=1)


def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def local(day, hour=0):
    return datetime(day.year, day.month, day.day, hour, tzinfo=config.zone())


# --- parsing ----------------------------------------------------------------------


def test_parse_reads_undated_and_dated_entries():
    steps = cadence.parse(["7d", "2027-03-08 1d", "2027-03-15 off"])
    assert steps == (
        Step(None, 7 * DAY),
        Step(date(2027, 3, 8), DAY),
        Step(date(2027, 3, 15), None),
    )
    assert [s.describe() for s in steps] == [
        "every 7d",
        "from 2027-03-08 every 1d",
        "from 2027-03-15 off",
    ]
    assert cadence.parse(["12h"]) == (Step(None, timedelta(hours=12)),)
    assert cadence.parse(["36h"])[0].describe() == "every 36h"
    assert cadence.parse([]) == ()


@pytest.mark.parametrize(
    "entries,complaint",
    [
        (["weekly"], "is not <n>d, <n>h, or off"),
        (["7w"], "is not <n>d, <n>h, or off"),
        (["0d"], "an interval of 0"),
        (["2027-03-08 1d", "7d"], "an undated entry comes first"),
        (["7d", "7d"], "an undated entry comes first"),
        (["2027-03-08"], "is not <n>d, <n>h, or off"),
        (["2027-03-08 1d extra"], "not <every> or <YYYY-MM-DD> <every>"),
        (["2027-13-01 1d"], "is not a YYYY-MM-DD date"),
        (["2027-03-15 off", "2027-03-08 1d"], "dates must increase"),
        (["2027-03-08 1d", "2027-03-08 off"], "dates must increase"),
    ],
)
def test_parse_refuses_a_malformed_entry_by_name(entries, complaint):
    with pytest.raises(config.ConfigError, match=complaint):
        cadence.parse(entries)


def test_the_setting_reads_from_the_environment(monkeypatch):
    monkeypatch.setenv("OUTLOOKS_SWEEP_CADENCE", "7d, 2027-03-08 1d")
    assert config.settings().sweep_cadence == ("7d", "2027-03-08 1d")
    monkeypatch.delenv("OUTLOOKS_SWEEP_CADENCE")
    assert config.settings().sweep_cadence == ()


# --- the due rule -----------------------------------------------------------------

EVENT = cadence.parse(["7d", "2027-03-08 1d", "2027-03-15 off"])


def test_due_once_the_interval_has_passed():
    now = local(date(2027, 2, 1), 12)
    assert cadence.next_due(EVENT, now - 8 * DAY, 0, now) == now
    assert cadence.next_due(EVENT, now - 2 * DAY, 0, now) == now + 5 * DAY


def test_a_tighter_step_brings_the_next_sweep_forward():
    now = local(date(2027, 3, 6), 12)
    # Weekly from the 5th would be the 12th; daily from the 8th starts then.
    assert cadence.next_due(EVENT, now - DAY, 0, now) == local(date(2027, 3, 8))


def test_off_now_is_never_and_off_later_cuts_the_interval_short():
    assert cadence.next_due(EVENT, None, 5, local(date(2027, 3, 17))) is None
    weekly_then_off = cadence.parse(["7d", "2027-03-08 off"])
    now = local(date(2027, 3, 4))
    assert cadence.next_due(weekly_then_off, now - DAY, 0, now) is None


def test_off_until_a_later_step_is_due_when_it_starts():
    later = cadence.parse(["off", "2027-03-08 1d"])
    now = local(date(2027, 2, 1))
    assert cadence.next_due(later, now - 30 * DAY, 0, now) == local(date(2027, 3, 8))
    dated = cadence.parse(["2027-03-08 1d"])
    assert cadence.in_force(dated, now) is None
    assert cadence.next_due(dated, None, 0, now) == local(date(2027, 3, 8))


def test_no_coverage_or_unclassified_mail_is_due_at_once():
    now = local(date(2027, 2, 1), 12)
    assert cadence.next_due(EVENT, None, 0, now) == now
    assert cadence.next_due(EVENT, now - DAY, 3, now) == now


def test_an_unset_cadence_schedules_nothing():
    now = local(date(2027, 2, 1))
    assert cadence.next_due((), None, 10, now) is None


# --- the command ------------------------------------------------------------------


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def swept(hours_ago):
    """A sweep that pulled its window ``hours_ago``, with every message classified."""
    end = datetime.now(UTC) - timedelta(hours=hours_ago)
    write(
        cv.coverage_csv(),
        [
            {
                "mailbox": BOX,
                "after": fmt_ts(end - 3 * DAY),
                "before": fmt_ts(end),
                "total": "0",
                "messages": "0",
                "pulled": "",
            }
        ],
    )
    write(
        cv.ledger_csv(),
        [{"internet_message_id": "<m>", "received": fmt_ts(end - DAY)}],
    )
    return end


def test_due_with_no_cadence_says_never_and_exits_1():
    result = runner.invoke(app, ["due"])
    assert result.exit_code == 1
    assert "cadence                 (unset)" in result.output
    assert result.output.splitlines()[-1] == "next sweep due          never"


def test_due_with_no_coverage_is_due_now(monkeypatch):
    monkeypatch.setenv("OUTLOOKS_SWEEP_CADENCE", "7d")
    result = runner.invoke(app, ["due"])
    assert result.exit_code == 0
    assert "last sweep              (no coverage)" in result.output
    assert result.output.splitlines()[-1] == "next sweep due          now"


def test_due_after_a_recent_sweep_names_the_instant(monkeypatch):
    monkeypatch.setenv("OUTLOOKS_SWEEP_CADENCE", "7d")
    end = swept(hours_ago=2)
    result = runner.invoke(app, ["due"])
    assert result.exit_code == 1, result.output
    assert "unclassified            0" in result.output
    want = fmt_ts(end + 7 * DAY)
    assert result.output.splitlines()[-1] == f"next sweep due          {want}"


def test_due_after_an_old_sweep_is_due_now(monkeypatch):
    monkeypatch.setenv("OUTLOOKS_SWEEP_CADENCE", "1d")
    swept(hours_ago=30)
    result = runner.invoke(app, ["due"])
    assert result.exit_code == 0
    assert result.output.splitlines()[-1] == "next sweep due          now"


def test_due_refuses_a_malformed_cadence(monkeypatch):
    monkeypatch.setenv("OUTLOOKS_SWEEP_CADENCE", "weekly")
    result = runner.invoke(app, ["due"])
    assert result.exit_code == 1
    assert "Error: sweep_cadence entry 'weekly'" in result.output


def test_due_refuses_a_missing_ledger_path(tmp_path):
    result = runner.invoke(app, ["due", "--ledger", str(tmp_path / "nope.csv")])
    assert result.exit_code == 1
    assert "names the sweep's ledger" in result.output


def test_doctor_fails_the_settings_check_on_a_malformed_cadence(tmp_path, monkeypatch):
    from outlooks import doctor

    (tmp_path / "pyproject.toml").write_text(
        '[tool.outlooks]\nmailbox = "desk@example.org"\n', encoding="utf-8"
    )
    monkeypatch.setenv("OUTLOOKS_SWEEP_CADENCE", "7d, 2027-13-01 1d")
    settings = next(r for r in doctor.run(tmp_path) if r.check == "settings")
    assert not settings.ok
    assert "is not a YYYY-MM-DD date" in settings.note


def test_a_cadence_that_is_not_a_list_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("OUTLOOKS_SWEEP_CADENCE", raising=False)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.outlooks]\nsweep_cadence = 7\n", encoding="utf-8"
    )
    with pytest.raises(config.ConfigError, match="must be a list of strings"):
        config.load(tmp_path)
