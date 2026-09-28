"""Tests for the committed Outlook archive: store, detail, classify.

Fixtures are synthetic messages shaped like the connector's own output: an
inquiry, a system sender's notice, one of our decision mails (body
shortened), and a search-hits page.
"""

import json
from pathlib import Path

import pytest

from outlooks import classify as cl
from outlooks import store

FIXTURES = Path(__file__).parent / "fixtures" / "outlook"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- store ----------------------------------------------------------------------


def test_id_hash_is_short_stable_and_filename_safe():
    mid = "<hash-001@mail.example.com>"
    assert store.id_hash(mid) == store.id_hash(mid)
    assert len(store.id_hash(mid)) == 16
    assert store.id_hash(mid).isalnum()


def test_save_message_is_idempotent_across_volatile_fields(tmp_path):
    message = load("inquiry.json")
    path, written = store.save_message(message, root=tmp_path)
    assert written and path.parent == tmp_path / "messages"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "isRead" not in saved and saved["subject"] == message["subject"]
    # A later read that only differs in read state and flag is the same message.
    reread = {**message, "isRead": True, "flag": {"flagStatus": "complete"}}
    assert store.save_message(reread, root=tmp_path) == (path, False)


def test_save_message_refuses_drift(tmp_path):
    message = load("inquiry.json")
    store.save_message(message, root=tmp_path)
    changed = {**message, "subject": "something else"}
    with pytest.raises(store.StoreError, match="different version"):
        store.save_message(changed, root=tmp_path)


def test_save_message_stores_attachment_texts_and_refuses_hits(tmp_path):
    message = load("inquiry.json")
    path, _ = store.save_message(message, ["the attached letter"], root=tmp_path)
    assert path.with_name(path.stem + ".1.txt").read_text() == "the attached letter"
    with pytest.raises(store.StoreError, match="empty"):
        store.save_message(message, ["the attached letter", "  "], root=tmp_path)
    with pytest.raises(store.StoreError, match="not a full read_resource"):
        store.save_message(load("hits-page.json")[0], root=tmp_path)


def test_save_hits_dedupes_groups_by_month_and_skips_the_trailer(tmp_path):
    page = load("hits-page.json")
    assert store.save_hits(page, root=tmp_path) == {"added": 3, "known": 0}
    assert store.save_hits(page, root=tmp_path) == {"added": 0, "known": 3}
    box = tmp_path / "hits" / "desk@example.org"
    lines = (box / "2026-06.jsonl").read_text().splitlines()
    assert len(lines) == 3
    received = [json.loads(x)["receivedDateTime"] for x in lines]
    assert received == sorted(received)
    hits = store.load_hits(tmp_path)
    assert all(h["mailbox"] == box.name for h in hits.values())
    assert "offset" not in next(iter(hits.values()))
    assert store.newest_hit(tmp_path) == "2026-06-18T16:00:20.000Z"


def test_save_hits_needs_a_mailbox_when_the_uri_has_no_owner(tmp_path):
    hit = {**load("hits-page.json")[0], "uri": "mail:///messages/x"}
    with pytest.raises(store.StoreError, match="pass --mailbox"):
        store.save_hits([hit], root=tmp_path)
    assert store.save_hits([hit], mailbox="a@b.c", root=tmp_path)["added"] == 1


def test_detail_reads_the_people_attachments_and_text_of_an_archived_message(tmp_path):
    from outlooks import detail as dt

    message = load("inquiry.json")
    store.save_message(message, root=tmp_path)
    d = dt.detail(message)
    sender = message["sender"]
    assert d["from"] == f"{sender['name']} <{sender['address']}>"
    assert d["to"] and "@" in d["to"]
    assert d["cc"] == "" and d["bcc"] == ""
    # The body comes back as text — no markup, no gateway banner, no wrappers.
    assert "<div" not in d["body"] and "ZjQcmQRYFpfpt" not in d["body"]
    assert "urldefense" not in d["body"]
    assert d["body"].strip()
    # And the archive reads back keyed by the id the correspondence rows carry.
    assert dt.details_by_id(tmp_path)[message["internetMessageId"]]["body"] == d["body"]


def test_detail_lists_only_the_files_the_sender_attached():
    from outlooks import detail as dt

    # Inline images are the signature logos and gateway banners embedded in
    # the HTML, not attachments; every archived message carries a few.
    message = {
        "body": {"contentType": "text", "content": "x"},
        "attachments": [
            {"name": "image.png", "contentType": "image/png", "isInline": True},
            {
                "name": "Outlook-abcd1234.png",
                "contentType": "image/png",
                "isInline": True,
            },
            {
                "name": "references.bib",
                "contentType": "application/octet-stream",
                "isInline": False,
            },
            {"name": "Notes.docx", "contentType": "application/msword"},
        ],
    }
    assert dt.detail(message)["attachments"] == ["references.bib", "Notes.docx"]


def test_body_text_breaks_lines_on_block_tags_and_keeps_a_plain_text_body():
    from outlooks import detail as dt

    nested = {
        "body": {"contentType": "html", "content": "<div>Dear X,<div>Line</div></div>"}
    }
    assert dt.body_text(nested) == "Dear X,\nLine"
    plain = {"body": {"contentType": "text", "content": "  Hello\n\nthere  "}}
    assert dt.body_text(plain) == "Hello\n\nthere"


def test_details_by_id_is_empty_when_nothing_is_archived(tmp_path):
    from outlooks import detail as dt

    assert dt.details_by_id(tmp_path) == {}


# --- classify -------------------------------------------------------------------


def test_classify_system_mail_by_its_sender_alone():
    mail = cl.view(load("system-notice.json"))
    assert cl.classify(mail) == cl.Facts("system")
    assert mail.recipients == ("desk@example.org",)
    # A reply prefix does not make a system sender's mail a follow-up.
    hit = {
        **load("hits-page.json")[0],
        "sender": "Notices@System.Example.org",
        "subject": "RE: New request received",
    }
    assert cl.classify(cl.view(hit)).role == "system"


def test_classify_decision_and_our_replies():
    decision = cl.view(load("decision-sent.json"))
    assert cl.classify(decision) == cl.Facts(
        "decision", outcome="Accepted with changes"
    )
    assert decision.day.isoformat() == "2026-06-15"  # 18:00Z is 11:00 in PT
    hit = load("hits-page.json")[2]
    reply = {
        **hit,
        "subject": "Re: [External] Re: [Decision]: Accepted with changes",
    }
    assert cl.classify(cl.view(reply)).role == "ours"


def test_classify_inbound_arrival_and_followup():
    hits = load("hits-page.json")
    assert cl.classify(cl.view(hits[1])) == cl.Facts("arrival")
    assert cl.classify(cl.view(hits[0])).role == "followup"  # "RE:" prefix
    assert cl.classify(cl.view(load("inquiry.json"))).role == "arrival"


def test_thread_subject_strips_stacked_prefixes():
    assert (
        cl.thread_subject("Re: [External] RE: Fwd: Inquiry: Winter Soil")
        == "inquiry: winter soil"
    )


def test_decision_with_nothing_after_the_tag_has_no_outcome():
    hit = {**load("hits-page.json")[2], "subject": "[Decision]:  "}
    assert cl.classify(cl.view(hit)) == cl.Facts("decision")


def test_classify_has_no_decision_role_when_the_tag_is_unset(tmp_path, monkeypatch):
    # A fresh cwd with no pyproject.toml of its own, so no [tool.outlooks] table
    # (real or otherwise) supplies a decision_tag once the env override is gone.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OUTLOOKS_DECISION_TAG", raising=False)
    assert cl.decision_tag() is None
    decision = cl.view(load("decision-sent.json"))
    # Still ours (the sender is a configured own_address), just never tagged
    # as a decision when no decision_tag is configured to look for.
    assert cl.classify(decision).role == "ours"
