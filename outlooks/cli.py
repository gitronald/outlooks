"""``outlooks`` — the Outlook mailbox archive and the skill that drives it.

    outlooks import [<capture-dir-or-file> ...] [--replace]
    outlooks coverage [--since YYYY-MM-DD] [--ledger <ledger.csv>]
    outlooks save <message.json> [--text <attachment.txt>] | <hits.json> --hits
    outlooks render <message.json> --out <dir> [--text <att.txt>]
    outlooks split <name> --match <term> [--since] [--batches <dir>]
    outlooks check <name>
    outlooks lookup-reset <name>
    outlooks worklist --after YYYY-MM-DD --before YYYY-MM-DD --out <dir>
    outlooks totals [<prefix> ...] | --check AFTER BEFORE
    outlooks audit <prefix> [--sweep <file>]
    outlooks windows init|fill|split|batches|remaining
    outlooks hash <internetMessageId>
    outlooks hook [--apply]
    outlooks config
    outlooks skill | doc | install    (from pkgskills)

Each command imports its working module inside the body, so ``--help`` and any
single command stay cheap. Every setting comes from ``[tool.outlooks]``
(``outlooks config``).

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from pkgskills import register

from outlooks.host import HOST

if TYPE_CHECKING:
    from outlooks.window_plan import Plan

app = typer.Typer(
    help="Archive and read an Outlook mailbox through the Microsoft 365 connector.",
    no_args_is_help=True,
)

# How many differing fields a DIFFERS line names before it counts the rest.
DIFFERS_SHOWN = 5


def _fail(message: object) -> typer.Exit:
    typer.echo(f"Error: {message}", err=True)
    return typer.Exit(1)


def _mailbox(mailbox: str | None) -> str:
    from outlooks import config

    try:
        return (mailbox or config.mailbox()).lower()
    except config.ConfigError as e:
        raise _fail(e) from None


@app.command("import")
def import_(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(help="Capture files or directories (default: the hook's)."),
    ] = None,
    mailbox: Annotated[str | None, typer.Option(help="Mailbox to import.")] = None,
    replace: Annotated[
        bool,
        typer.Option(
            "--replace",
            help="Rewrite stored hits and messages that differ from the capture.",
        ),
    ] = False,
) -> None:
    """Archive the capture hook's files and record the complete window pulls."""
    from outlooks import capture as cp
    from outlooks import coverage as cv

    box = _mailbox(mailbox)
    captures, other = cp.load_captures(paths or [cp.captured_dir()])
    done = cp.import_captures(captures, mailbox=box, replace=replace)
    typer.echo(
        f"{len(captures)} captures ({done.other_mailbox} other mailbox), "
        f"{len(other)} other files skipped"
    )
    for path in done.lost:
        typer.echo(
            f"  LOST capture (its output can't be recovered; call again): {path}"
        )
    typer.echo(f"hits: {done.hits_added} added, {done.hits_known} known")
    for mid, old, new in done.hits_replaced:
        moved = f" -> {new}" if new != old else ""
        typer.echo(f"  REPLACED hit {mid} in {old}{moved}")
    typer.echo(
        f"messages: {done.messages_written} written, "
        f"{done.messages_unchanged} unchanged"
    )
    for path in done.messages_replaced:
        typer.echo(f"  REPLACED message from {path}")
    for path, fields in done.messages_differ:
        shown = ", ".join(fields[:DIFFERS_SHOWN])
        if len(fields) > DIFFERS_SHOWN:
            shown += f" (+{len(fields) - DIFFERS_SHOWN} more)"
        where = f" in {shown}" if shown else ""
        typer.echo(f"  DIFFERS from the stored copy{where}: {path}")
    typer.echo(f"attachment texts: {done.attachments_written} written")
    for path in done.attachments_unmatched:
        typer.echo(f"  UNMATCHED attachment read (its message not read): {path}")
    for path, why in done.attachments_refused:
        typer.echo(f"  REFUSED attachment read ({why}): {path}")
    found = cv.windows(captures, box)
    try:
        rows = cv.record(found)
    except cv.CoverageError as e:
        raise _fail(e) from None
    # Only an incomplete window outside the recorded coverage needs paging; one
    # inside it is a probe or a superseded pull, so it is counted, not listed.
    spans = cv.merge([r for r in cv.read_rows() if r["mailbox"] == box])
    recorded = {(r["after"], r["before"], r["total"]) for r in rows}
    known = inside = 0
    for w in found:
        if w.complete:
            row = w.row()
            known += (row["after"], row["before"], row["total"]) not in recorded
        elif any(a <= w.after and w.end <= b for a, b in spans):
            inside += 1
        else:
            got = (
                f"{len(w.offsets)} offsets captured, its last page missing"
                if w.total is None
                else f"{len(w.offsets)} of {w.total} offsets captured"
            )
            typer.echo(f"  INCOMPLETE window {cv.fmt_ts(w.after)}: {got}")
    for row in rows:
        typer.echo(
            f"  COVERED {row['after']} to {row['before']}: "
            f"{row['total']} copies, {row['messages']} messages"
        )
    typer.echo(f"coverage: {len(rows)} windows recorded")
    if known or inside:
        typer.echo(
            f"  ({known} complete windows already recorded; {inside} incomplete "
            f"windows inside recorded coverage not listed)"
        )


@app.command()
def coverage(
    mailbox: Annotated[str | None, typer.Option(help="Mailbox to report.")] = None,
    since: Annotated[
        str | None,
        typer.Option(help="Report gaps from this date (default: the first row)."),
    ] = None,
    ledger: Annotated[
        Path | None,
        typer.Option(help="The sweep's ledger (default: ledger.csv in archive_dir)."),
    ] = None,
) -> None:
    """Report which windows the archive holds completely, and the gaps (read-only)."""
    from datetime import datetime

    from outlooks import config, store
    from outlooks import coverage as cv

    box = _mailbox(mailbox)
    start = None
    if since:
        try:
            start = datetime.fromisoformat(since).replace(tzinfo=config.zone())
        except ValueError:
            raise _fail(f"{since!r} is not a YYYY-MM-DD date") from None
    hits = store.load_hits()
    lines = cv.report(
        box,
        cv.read_rows(),
        hits,
        set(store.load_messages()),
        cv.read_rows(ledger or cv.ledger_csv()),
        since=start,
        skipped=cv.mass_copies(hits, cv.read_rows(cv.mass_sends_csv())),
    )
    typer.echo("\n".join(lines))


@app.command()
def save(
    payload: Annotated[
        Path,
        typer.Argument(
            help="A read_resource message JSON, or with --hits a search page."
        ),
    ],
    text: Annotated[
        list[Path] | None,
        typer.Option(help="Attachment text, in attachment order (repeatable)."),
    ] = None,
    hits: Annotated[
        bool, typer.Option("--hits", help="PAYLOAD is a list of search hits.")
    ] = False,
    mailbox: Annotated[
        str | None,
        typer.Option(help="Mailbox the hits came from (default: the uri's owner)."),
    ] = None,
) -> None:
    """Add a hand-saved message or search page to the archive (sessions use import)."""
    import json

    from outlooks import store

    try:
        data = json.loads(payload.read_text(encoding="utf-8"))
        if hits:
            if not isinstance(data, list):
                raise store.StoreError("--hits takes a JSON list of search hits")
            counts = store.save_hits(data, mailbox=mailbox)
            typer.echo(f"hits: {counts['added']} added, {counts['known']} known")
            return
        texts = [t.read_text(encoding="utf-8") for t in text or []]
        path, written = store.save_message(data, texts)
    except (store.StoreError, ValueError, OSError) as e:
        raise _fail(e) from None
    typer.echo(f"{'Wrote' if written else 'Unchanged'} {path}")


@app.command()
def render(
    message: Annotated[
        Path, typer.Argument(help="Message JSON saved from read_resource.")
    ],
    out: Annotated[
        Path, typer.Option(help="Directory to write the .docx into.")
    ] = Path("."),
    surname: Annotated[
        str | None,
        typer.Option(help="Surname for the file name (default: sender's last name)."),
    ] = None,
    text: Annotated[
        Path | None,
        typer.Option(help="Attachment text to use in place of the message body."),
    ] = None,
    label: Annotated[
        str | None,
        typer.Option(
            help="Label for the file (Message, Letter, Report, ...); "
            "default: render_label in [tool.outlooks], else Message."
        ),
    ] = None,
    stage: Annotated[
        str | None,
        typer.Option(help="Stage (e.g. Initial) for a non-standalone label."),
    ] = None,
) -> None:
    """Transcribe a message to its ``{Surname} - {Label}.docx``; never overwrite."""
    import json

    from outlooks.render import render as run

    try:
        data = json.loads(message.read_text(encoding="utf-8"))
        body = text.read_text(encoding="utf-8") if text else None
        path = run(data, out, surname=surname, text=body, label=label, stage=stage)
    # KeyError: a JSON file that isn't a full read_resource message (e.g. a
    # search hit, which carries no body/sender) must report the missing field,
    # not crash with a traceback.
    except (ValueError, OSError, KeyError) as e:
        raise _fail(e) from None
    typer.echo(f"Wrote {path}")


@app.command()
def split(
    name: Annotated[str, typer.Argument(help="The lookup's name (page-file prefix).")],
    match: Annotated[
        list[str],
        typer.Option(help="A term a hit must contain (repeatable, case-insensitive)."),
    ],
    since: Annotated[
        str | None, typer.Option(help="Keep hits received on or after YYYY-MM-DD.")
    ] = None,
    match_summary: Annotated[
        bool,
        typer.Option(help="Also match the hit's summary (people written about)."),
    ] = True,
    batches: Annotated[
        Path | None,
        typer.Option(help="Write the to-read hits two per {name}-batch-NN.txt here."),
    ] = None,
) -> None:
    """Split a lookup's hits into archived (timeline written) and to-read."""
    from outlooks import lookup

    result = lookup.split(
        name, match, since=since, match_summary=match_summary, batches=batches
    )
    to_read = {nn for nn, _ in result.to_read}
    for nn, h in sorted([*result.known, *result.to_read], key=lambda x: x[0]):
        state = "READ " if nn in to_read else "known"
        typer.echo(
            f"{nn} {state} {h['receivedDateTime']} {h['sender']} | "
            f"{(h.get('subject') or '')[:50]}"
        )
        if nn in to_read:
            typer.echo(f"     {h['uri']}")
    for path in result.batches:
        typer.echo(f"wrote {path}")
    typer.echo(
        f"{result.total} hits matched from {result.pages} page files: "
        f"{len(result.known)} archived, {len(result.to_read)} to read"
    )


@app.command()
def check(
    name: Annotated[str, typer.Argument(help="The lookup's name.")],
) -> None:
    """Check a lookup's timeline files against the archive; exit 1 on any finding."""
    from outlooks import config, lookup

    rows = lookup.check(name)
    show_system = bool(config.settings().system_senders)
    for r in rows:
        system = f" system={r.system}" if show_system else ""
        typer.echo(
            f"{r.nn} {r.received or '-'} {r.day or '-'} {'ok' if r.ok else 'BAD'} "
            f"{r.direction:3}{system} txt={r.texts} {r.subject[:60]}"
        )
        if r.finding:
            typer.echo(f"   {r.finding}")
    if not rows:
        typer.echo(f"no {name}-NN.timeline.json files in {config.scratch_dir()}")
    raise typer.Exit(0 if rows and all(r.ok for r in rows) else 1)


@app.command("lookup-reset")
def lookup_reset(
    name: Annotated[str, typer.Argument(help="The lookup's name.")],
) -> None:
    """Move a lookup's page and timeline files aside before a repeat lookup."""
    from outlooks import config, lookup

    folder, moved = lookup.reset(name)
    for path in moved:
        typer.echo(f"moved {path}")
    if moved:
        typer.echo(f"{len(moved)} files of {name} moved to {folder}")
    else:
        typer.echo(f"no files of {name} in {config.scratch_dir()}")


@app.command()
def worklist(
    after: Annotated[str, typer.Option(help="First local date, YYYY-MM-DD.")],
    before: Annotated[str, typer.Option(help="Local date after the last, exclusive.")],
    out: Annotated[Path, typer.Option(help="Directory for the batch-NN.txt files.")],
    per: Annotated[int, typer.Option(help="Messages per batch file.")] = 10,
    name: Annotated[str, typer.Option(help="Line-name prefix.")] = "msg",
) -> None:
    """Reader worklists for a full read of a range (window mode's bulk read)."""
    from outlooks import worklist as wl

    try:
        result = wl.read_worklist(after, before, out, per=per, name=name)
    except wl.WorklistError as e:
        raise _fail(e) from None
    typer.echo(
        f"{result.listed} messages listed by window captures, "
        f"{len(result.todo)} to read"
    )
    typer.echo(f"{len(result.batches)} batch files in {out}")
    if result.mass_copies:
        typer.echo(f"{result.mass_copies} mass-send copies skipped (mass-sends.csv)")
    if result.orphans:
        typer.echo(
            f"{result.orphans} archived hits in range that no window capture lists"
        )
    for hit in result.todo:
        typer.echo(f"{hit['receivedDateTime']} {(hit.get('subject') or '')[:70]}")


@app.command()
def totals(
    prefixes: Annotated[
        list[str] | None,
        typer.Argument(help="afterDateTime prefixes (2023, 2021-1); default all."),
    ] = None,
    check_range: Annotated[
        tuple[str, str] | None,
        typer.Option(
            "--check",
            metavar="AFTER BEFORE",
            help="Run the item-id proof over this range instead.",
        ),
    ] = None,
) -> None:
    """Window sizes from the captures, or the item-id proof for a range."""
    from outlooks.capture import captured_dir, load_captures
    from outlooks.coverage import fmt_ts, parse_ts
    from outlooks.sizing import id_check, window_sizes

    box = _mailbox(None)
    captures, _ = load_captures(sorted(captured_dir().glob("*.json")))
    if check_range:
        try:
            after, before = (parse_ts(t) for t in check_range)
        except ValueError as e:
            raise _fail(e) from None
        result = id_check(captures, box, after, before)
        baseline = "?" if result.baseline is None else str(result.baseline)
        typer.echo(
            f"range {fmt_ts(after)} {fmt_ts(before)} baseline {baseline} "
            f"ids {result.ids} messages {result.messages} windows {result.windows}"
        )
        if result.baseline is None:
            typer.echo("no capture of the whole range: probe it with limit 1 first")
        else:
            typer.echo("ok" if result.ok else "mismatch")
        raise typer.Exit(0 if result.ok else 1)
    sizes = window_sizes(captures, box)
    wanted = tuple(prefixes or ()) or ("",)
    # Open-ended windows sort after the bounded ones that start with them.
    for (after, before), total in sorted(
        sizes.items(), key=lambda item: (item[0][0], item[0][1] is None, item[0][1])
    ):
        if fmt_ts(after).startswith(wanted):
            typer.echo(f"{fmt_ts(after)} {fmt_ts(before) if before else '-'} {total}")


@app.command()
def audit(
    prefix: Annotated[
        str, typer.Argument(help="receivedDateTime prefix (2025, 2024-03).")
    ],
    sweep: Annotated[
        Path | None,
        typer.Option(help="Write the never-attempted attachments as a worklist."),
    ] = None,
) -> None:
    """Attachment-text audit for the archived messages of a period."""
    from outlooks import worklist as wl

    result = wl.audit(prefix)
    typer.echo(str(dict(result.counts)))
    typer.echo(f"missing by type: {dict(result.missing)}")
    for line in result.listed:
        typer.echo(f"  MISSING {line}")
    typer.echo(f"never attempted: {len(result.untried)}")
    for name, ctype, _ in result.untried:
        typer.echo(f"  UNTRIED {name} {ctype}")
    if sweep:
        sweep.write_text(
            "".join(f"att-{name} {uri}\n" for name, _, uri in result.untried),
            encoding="utf-8",
        )
    typer.echo(f"text in an image slot: {result.image_slots or 'none'}")
    typer.echo(f"unparseable: {result.bad or 'none'}")


@app.command("hash")
def hash_(
    message_id: Annotated[str, typer.Argument(help="An internetMessageId.")],
) -> None:
    """The archive file stem for a message: ``messages/{hash}.json``."""
    from outlooks import store

    typer.echo(store.id_hash(message_id))


@app.command()
def hook(
    apply: Annotated[
        bool, typer.Option("--apply", help="Write the script and settings entry.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Replace a script that differs.")
    ] = False,
) -> None:
    """Check (or with --apply, wire) the PostToolUse capture hook in this repo."""
    from pkgskills import find_repo_root

    from outlooks import hook as hk

    root = find_repo_root(Path.cwd())
    if apply:
        done = hk.apply(root, force=force, start=Path.cwd())
        for line in done or ["nothing to do"]:
            typer.echo(line)
    now = hk.status(root, Path.cwd())
    typer.echo(f"script    {now.script:8} {hk.SCRIPT_PATH}")
    typer.echo(f"settings  {now.settings:8} {hk.SETTINGS_PATH}")
    if now.advice:
        typer.echo(now.advice)
    raise typer.Exit(0 if now.ok else 1)


@app.command("config")
def config_() -> None:
    """Print the effective settings and where each came from."""
    from outlooks import config

    s = config.settings()
    typer.echo(f"table: {s.source or '(none: defaults)'}")
    for key, value, origin in config.describe():
        typer.echo(f"{key:15} {value}  [{origin}]")


windows_app = typer.Typer(
    help="Plan a window backfill: size, split, and page a fixed range.",
    no_args_is_help=True,
)
app.add_typer(windows_app, name="windows")


def _state_dir() -> Path:
    from outlooks import config

    return config.scratch_dir() / "windows"


def _plan(folder: Path) -> Plan:
    from outlooks import window_plan as wp

    try:
        return wp.load(folder)
    except wp.PlanError as e:
        raise _fail(e) from None


@windows_app.command("init")
def windows_init(
    after: Annotated[str, typer.Argument(help="YYYY-MM-DD or a full timestamp.")],
    before: Annotated[
        str | None, typer.Argument(help="Default: now (UTC, to the second).")
    ] = None,
) -> None:
    """Start a plan over a fixed range; print the range probe and the month pieces."""
    from datetime import UTC, datetime

    from outlooks import coverage as cv
    from outlooks import window_plan as wp

    folder = _state_dir()
    if (folder / "windows.json").exists():
        raise _fail("windows.json already exists; remove it to start over")
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        lo = wp.parse_bound(after)
        hi = wp.parse_bound(before) if before else now
        plan = wp.init(_mailbox(None), lo, hi)
    except wp.PlanError as e:
        raise _fail(e) from None
    wp.save(plan, folder)
    typer.echo(f"range {cv.fmt_ts(plan.after)} {cv.fmt_ts(plan.before)}")
    for piece in plan.pieces:
        typer.echo(f"{cv.fmt_ts(piece.after)} {cv.fmt_ts(piece.before)}")


@windows_app.command("fill")
def windows_fill() -> None:
    """Apply the sizes the captures carry; list the pieces still unsized."""
    from outlooks import coverage as cv
    from outlooks import window_plan as wp
    from outlooks.capture import captured_dir, load_captures
    from outlooks.sizing import window_sizes

    folder = _state_dir()
    plan = _plan(folder)
    captures, _ = load_captures(sorted(captured_dir().glob("*.json")))
    wp.fill(plan, window_sizes(captures, _mailbox(None)))
    wp.save(plan, folder)
    total = "?" if plan.total is None else str(plan.total)
    typer.echo(f"range {cv.fmt_ts(plan.after)} {cv.fmt_ts(plan.before)} {total}")
    known = [p.total for p in plan.pieces if p.total is not None]
    unknown = [p for p in plan.pieces if p.total is None]
    over = sum(1 for n in known if n > wp.LIMIT)
    typer.echo(
        f"pieces {len(plan.pieces)} sum {sum(known)} "
        f"unknown {len(unknown)} over-LIMIT {over}"
    )
    for piece in unknown:
        typer.echo(f"{cv.fmt_ts(piece.after)} {cv.fmt_ts(piece.before)}")


@windows_app.command("split")
def windows_split() -> None:
    """Split every piece over the limit; print the new pieces to size."""
    from outlooks import coverage as cv
    from outlooks import window_plan as wp

    folder = _state_dir()
    plan = _plan(folder)
    new, unsplittable = wp.split(plan)
    wp.save(plan, folder)
    for piece in new:
        typer.echo(f"{cv.fmt_ts(piece.after)} {cv.fmt_ts(piece.before)}")
    for piece in unsplittable:
        typer.echo(
            f"unsplittable {cv.fmt_ts(piece.after)} {cv.fmt_ts(piece.before)}",
            err=True,
        )


@windows_app.command("batches")
def windows_batches() -> None:
    """Pack the uncovered pieces into pager-NN.txt files beside the plan."""
    from outlooks import coverage as cv
    from outlooks import window_plan as wp

    folder = _state_dir()
    plan = _plan(folder)
    try:
        grouped = wp.batches(plan, cv.read_rows())
    except wp.PlanError as e:
        raise _fail(e) from None
    written = wp.write_batches(grouped, folder)
    count = sum(len(g) for g in grouped)
    pages = sum(wp.pages(p) for g in grouped for p in g)
    typer.echo(f"{len(grouped)} batches, {count} windows, {pages} pages")
    for path in written:
        typer.echo(str(path))


@windows_app.command("remaining")
def windows_remaining() -> None:
    """List the pieces coverage.csv does not cover yet."""
    from outlooks import coverage as cv
    from outlooks import window_plan as wp

    left = wp.remaining(_plan(_state_dir()), cv.read_rows())
    for piece in left:
        total = "?" if piece.total is None else str(piece.total)
        typer.echo(f"{cv.fmt_ts(piece.after)} {cv.fmt_ts(piece.before)} {total}")
    typer.echo(f"{len(left)} pieces not covered", err=True)


register(app, HOST)
