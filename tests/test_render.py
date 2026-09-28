"""Tests for rendering an Outlook message as a filed transcription."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from outlooks import render
from outlooks.cli import app

FIXTURES = Path(__file__).parent / "fixtures" / "outlook"
MESSAGE = FIXTURES / "inquiry.json"
needs_pandoc = pytest.mark.skipif(
    shutil.which("pandoc") is None, reason="pandoc not installed"
)


@pytest.fixture
def message():
    """A body-only message as read_resource returns it (security banner included)."""
    return json.loads(MESSAGE.read_text(encoding="utf-8"))


def plain(path: Path) -> str:
    return subprocess.run(
        ["pandoc", "-t", "plain", "--wrap=none", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_clean_body_drops_the_banner_and_preheader(message):
    body = render.clean_body(message["body"]["content"])
    assert body.startswith("<p>Dear organizers:</p>")
    for leftover in (
        "Untrusted Sender",
        "Report Suspicious",
        "ZjQcmQRY",
        "pfptBanner",
        "display:none",
    ):
        assert leftover not in body


def test_clean_body_unwraps_url_defense_links(message):
    body = render.clean_body(message["body"]["content"])
    assert "urldefense" not in body
    assert '<a href="https://profiles.example.net/priya-nakamura-garden/">' in body
    assert '<a href="https://nakamuragarden.example">' in body


def test_clean_body_decodes_a_link_whose_text_is_not_a_url():
    html = (
        '<p><a href="https://urldefense.com/v3/__https://example.org/notes__;'
        '!!abc$">the notes</a></p>'
    )
    assert render.clean_body(html) == (
        '<p><a href="https://example.org/notes">the notes</a></p>'
    )


def test_clean_body_drops_a_preheader_without_a_banner():
    html = '<div style="display:none;color:#fff">Preview text</div><p>Hi</p>'
    assert render.clean_body(html) == "<p>Hi</p>"


def test_clean_body_prefers_the_link_text_when_it_is_a_url():
    html = (
        '<a href="https://urldefense.com/v3/__https://example.org/a*b__;Xw!!x$">'
        "https://example.org/shown</a>"
    )
    assert render.clean_body(html) == (
        '<a href="https://example.org/shown">https://example.org/shown</a>'
    )


def test_clean_body_decodes_proofpoint_escapes():
    # `**A` is a run of 2 bytes from the token after `__;` ("w7M" is the
    # UTF-8 for "ó"); a synthetic staff-directory link.
    html = (
        '<a href="https://urldefense.com/v3/__https://www.example-univ.edu/faculty/'
        'people/501/**A-doyle-marin__;w7M!!abcDEF123456ghiJKL!eLRd$">'
        "Example University</a>"
    )
    assert render.clean_body(html) == (
        '<a href="https://www.example-univ.edu/faculty/people/501/ó-doyle-marin">'
        "Example University</a>"
    )


def test_clean_body_decodes_a_single_byte_escape():
    # A lone `*` is one byte from the token ("Xw" is "_").
    html = (
        '<a href="https://urldefense.com/v3/__https://example.org/a*b__;Xw!!x$">'
        "the notes</a>"
    )
    assert render.clean_body(html) == (
        '<a href="https://example.org/a_b">the notes</a>'
    )


def test_clean_body_escapes_a_query_ampersand_once():
    html = (
        '<a href="https://urldefense.com/v3/__https://scholar.example.org/c?'
        'user=YJ8&amp;hl=en__;!!abcDEF$">Profile page</a>'
    )
    assert render.clean_body(html) == (
        '<a href="https://scholar.example.org/c?user=YJ8&amp;hl=en">Profile page</a>'
    )


def test_clean_body_leaves_an_undecodable_link_wrapped():
    # `**B` asks for 3 bytes, but the token holds only 2.
    html = (
        '<a href="https://urldefense.com/v3/__https://example.org/a**Bb__;w7M!!x$">'
        "the notes</a>"
    )
    assert render.clean_body(html) == html


def test_clean_body_leaves_a_plain_message_alone():
    html = '<p>Hello <a href="https://example.org">site</a></p>'
    assert render.clean_body(html) == html


def test_clean_body_keeps_sender_text_above_a_quoted_banner():
    """The regression guard: the cut starts at the banner, not at the document.

    A sender who has corresponded before gets no fresh banner, but their reply
    quotes an earlier bannered mail — so the only end marker sits *below* the
    new text. Cutting from the top of the document deleted the whole message, and
    the truncated transcription is what gets filed as the permanent record.
    """
    html = (
        "<p>Dear organizers, here is my note.</p>"
        "<blockquote>"
        '<div id="x">ZjQcmQRYFpfptBannerStart This Message Is From an Untrusted '
        "Sender ZjQcmQRYFpfptBannerEnd</div>"
        "<p>Older text</p>"
        "</blockquote>"
    )
    cleaned = render.clean_body(html)
    assert "Dear organizers, here is my note." in cleaned
    assert "Untrusted" not in cleaned and "ZjQcmQRY" not in cleaned


def test_clean_body_unwraps_links_whose_href_is_not_first():
    # Thunderbird puts class= before href=; other clients emit target= first or
    # single-quote the value. All three stayed wrapped in the filed .docx.
    for tag in (
        '<a class="moz-txt-link-freetext" href="https://urldefense.com/v3/'
        '__https://example.org/p__;!!x$">the notes</a>',
        '<a target="_blank" href="https://urldefense.com/v3/'
        '__https://example.org/p__;!!x$">the notes</a>',
        "<a href='https://urldefense.com/v3/__https://example.org/p__;!!x$'>"
        "the notes</a>",
    ):
        cleaned = render.clean_body(tag)
        assert 'href="https://example.org/p"' in cleaned, tag
        assert "urldefense" not in cleaned, tag


def test_clean_body_unwraps_the_proofpoint_com_host():
    # Both gateway hostnames rewrite to the same v3 format; only one was decoded.
    html = (
        '<a href="https://urldefense.proofpoint.com/v3/__https://example.org/p__;'
        '!!x$">the notes</a>'
    )
    assert render.clean_body(html) == '<a href="https://example.org/p">the notes</a>'


def test_text_body_strips_the_banner_and_unwraps_links():
    # A text/plain body never reached clean_body, so the gateway's banner and
    # raw urldefense URLs were transcribed verbatim into the filed file.
    text = (
        "ZjQcmQRYFpfptBannerStart\n"
        "This Message Is From an Untrusted Sender\n"
        "ZjQcmQRYFpfptBannerEnd\n"
        "Dear organizers,\n"
        "My site: https://urldefense.com/v3/__https://example.org/a*b__;Xw!!x$\n"
    )
    out = render.text_html(text)
    assert "Untrusted" not in out and "ZjQcmQRY" not in out
    assert "<p>Dear organizers,</p>" in out
    assert "https://example.org/a_b" in out and "urldefense" not in out


# --- file naming -------------------------------------------------------------


def test_default_surname_handles_directory_and_pronoun_forms():
    # "Doe, Jane" filed under the first name; "(she/her)" became the file name
    # and its "/" split the path in two.
    assert render.default_surname("Doe, Jane") == "Doe"
    assert render.default_surname("Jane Doe (she/her)") == "Doe"
    assert render.default_surname("Priya Nakamura") == "Nakamura"


def test_check_surname_refuses_a_name_that_would_change_the_path():
    for bad in ("she/her", "..", "a\\b", ""):
        with pytest.raises(ValueError):
            render.check_surname(bad)


def test_docx_name_uses_the_stage_when_one_is_given(message):
    # A label with a stage is filed under it, not as a standalone label.
    assert render.docx_name(message) == "Nakamura - Message.docx"
    assert (
        render.docx_name(message, stage="Initial", label="Cover Letter")
        == "Nakamura - Initial - Cover Letter.docx"
    )


def test_docx_name_rejects_an_unusable_sender_name(message):
    message["sender"]["name"] = "(unknown)"
    with pytest.raises(ValueError, match="no usable display name"):
        render.docx_name(message)


def test_header_lists_sender_recipients_and_pacific_send_time(message):
    header = render.header_html(message)
    assert (
        "<strong>From:</strong> Priya Nakamura &lt;priya.nakamura@example.com&gt;"
        in header
    )
    assert "<strong>Sent:</strong> Wednesday, June 17, 2026 2:30 PM (PT)" in header
    assert "<strong>To:</strong> desk@example.org" in header
    assert "<strong>Subject:</strong> Inquiry: Cover Crops and Winter Soil" in header


def test_header_uses_standard_time_in_winter(message):
    message["sentDateTime"] = "2026-01-15T20:05:00Z"
    assert "Thursday, January 15, 2026 12:05 PM (PT)" in render.header_html(message)


def test_header_lists_cc_recipients_when_there_are_any(message):
    assert "<strong>Cc:</strong>" not in render.header_html(message)
    message["ccRecipients"] = [{"name": "Second Person", "address": "co@example.org"}]
    assert "<strong>Cc:</strong> co@example.org" in render.header_html(message)


def test_docx_name_defaults_to_the_sender_surname(message):
    assert render.docx_name(message) == "Nakamura - Message.docx"
    assert render.docx_name(message, surname="Brown") == "Brown - Message.docx"


def test_text_body_splits_paragraphs_on_blank_lines():
    assert render.text_html("First para.\n\n  Second & last.\n") == (
        "<p>First para.</p>\n<p>Second &amp; last.</p>"
    )


def test_text_body_rejoins_hard_wrapped_lines():
    # A plain-text message is wrapped near 72 columns; one <p> per line turned every
    # wrapped line into its own paragraph in the filed transcription.
    body = (
        "Dear organizers,\n"
        "I am writing to inquire whether the following proposed workshop\n"
        "would be a good fit for your spring program.\n"
        "\n"
        "Tentative title: When Fallow Beds Become Fields.\n"
    )
    assert render.text_html(body) == (
        "<p>Dear organizers,</p>\n"
        "<p>I am writing to inquire whether the following proposed workshop "
        "would be a good fit for your spring program.</p>\n"
        "<p>Tentative title: When Fallow Beds Become Fields.</p>"
    )


def test_text_body_keeps_short_lines_separate():
    # Signature and address blocks are short lines; rejoining them would run the
    # whole block into one paragraph.
    signature = "Best regards,\nPriya D. Nakamura\nThe Nakamura Group\n"
    assert render.text_html(signature) == (
        "<p>Best regards,</p>\n<p>Priya D. Nakamura</p>\n<p>The Nakamura Group</p>"
    )


def test_decode_v3_keeps_a_query_parameter_that_looks_like_an_entity():
    # html.unescape resolves HTML5's semicolon-less references, so these query
    # strings came back corrupted into the permanent filed .docx.
    for query, mangled in (
        ("region=us", "®ion=us"),
        ("copy=full", "©=full"),
        ("times=3", "×=3"),
    ):
        wrapped = f"https://urldefense.com/v3/__https://ex.org/c?a=1&{query}__;!!x$"
        decoded = render.decode_v3(wrapped)
        assert decoded is not None
        assert decoded == f"https://ex.org/c?a=1&{query}"
        assert mangled not in decoded


def test_decode_v3_still_decodes_a_real_entity():
    wrapped = "https://urldefense.com/v3/__https://ex.org/c?a=1&amp;b=2__;!!x$"
    assert render.decode_v3(wrapped) == "https://ex.org/c?a=1&b=2"


@needs_pandoc
def test_render_matches_the_reviewed_dry_run(message, tmp_path):
    out = render.render(message, tmp_path)
    assert out == tmp_path / "Nakamura - Message.docx"
    assert plain(out) == (FIXTURES / "inquiry.txt").read_text(encoding="utf-8")


@needs_pandoc
def test_render_uses_attachment_text_in_place_of_the_body(message, tmp_path):
    out = render.render(message, tmp_path, text="Attached letter, paragraph one.\n")
    text = plain(out)
    assert "Subject: Inquiry" in text
    assert "Attached letter, paragraph one." in text
    assert "Dear organizers" not in text


@needs_pandoc
def test_render_handles_a_plain_text_message(message, tmp_path):
    message["body"] = {"contentType": "text", "content": "Dear organizers,\n\nA & B.\n"}
    text = plain(render.render(message, tmp_path))
    assert "Dear organizers,\n\nA & B." in text


def test_render_runs_pandoc_sandboxed(message, tmp_path, monkeypatch):
    """The body is attacker-controlled HTML, so pandoc must not read local files.

    Without --sandbox an <img src="/path/to/.env"> makes pandoc embed that
    file's bytes in the .docx — which is then filed where the sender can
    read it.
    """
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"docx")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(render.shutil, "which", lambda _: "/usr/bin/pandoc")
    monkeypatch.setattr(render.subprocess, "run", fake_run)
    render.render(message, tmp_path)
    assert "--sandbox" in seen["cmd"]


@needs_pandoc
def test_render_does_not_embed_a_local_file_the_body_points_at(message, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("APP_PASSWORD=hunter2", encoding="utf-8")
    message["body"]["content"] = f'<p>Hi</p><img src="{secret}">'
    try:
        out = render.render(message, tmp_path)
    except ValueError:
        return  # refusing outright is also a safe outcome
    assert b"hunter2" not in out.read_bytes()


def test_render_refuses_a_body_with_no_text(message, tmp_path):
    # A header-only .docx reporting success would be filed as though it were
    # the sender's message.
    with pytest.raises(ValueError, match="no body text"):
        render.render(message, tmp_path, text="   \n\n")
    assert list(tmp_path.iterdir()) == []


def test_render_refuses_to_overwrite(message, tmp_path):
    existing = tmp_path / "Nakamura - Message.docx"
    existing.write_bytes(b"earlier copy")
    with pytest.raises(ValueError, match="already exists"):
        render.render(message, tmp_path)
    assert existing.read_bytes() == b"earlier copy"


@needs_pandoc
@pytest.mark.parametrize(
    ("table", "env", "flag", "name"),
    [
        (None, None, None, "Nakamura - Message.docx"),
        ("Letter", None, None, "Nakamura - Letter.docx"),
        ("Letter", "Note", None, "Nakamura - Note.docx"),
        ("Letter", "Note", "Report", "Nakamura - Report.docx"),
    ],
)
def test_cli_render_label_defaults_to_the_configured_one(
    tmp_path, monkeypatch, table, env, flag, name
):
    # The default, then the table, then the environment; --label wins over all.
    if table:
        (tmp_path / "pyproject.toml").write_text(
            f'[tool.outlooks]\nrender_label = "{table}"\n', encoding="utf-8"
        )
    if env:
        monkeypatch.setenv("OUTLOOKS_RENDER_LABEL", env)
    out = tmp_path / "out"
    args = ["render", str(MESSAGE), "--out", str(out)]
    result = CliRunner().invoke(app, args + (["--label", flag] if flag else []))
    assert result.exit_code == 0, result.output
    assert [p.name for p in out.iterdir()] == [name]


def test_docx_name_takes_the_configured_label(message, monkeypatch):
    # The same default without pandoc, so it runs wherever the suite does.
    monkeypatch.setenv("OUTLOOKS_RENDER_LABEL", "Letter")
    assert render.docx_name(message) == "Nakamura - Letter.docx"
    assert render.docx_name(message, label="Report") == "Nakamura - Report.docx"


@needs_pandoc
def test_cli_render_writes_the_docx(tmp_path):
    result = CliRunner().invoke(app, ["render", str(MESSAGE), "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "Nakamura - Message.docx").exists()
    assert "Nakamura - Message.docx" in result.output
