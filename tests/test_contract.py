"""The Python API a consuming repo may import, pinned.

A repo that imports this package depends on these names, and on the names of
these functions' parameters, which of them it may leave out, and that it may
pass each by position or by name. A failure here is a change a consumer must hear
about: make it deliberately, edit this file with it, and give it a changelog
entry under *Changed* or *Removed*.
"""

import dataclasses
import importlib
import inspect
import json
import pkgutil
import re
from pathlib import Path

import pytest

import outlooks

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "outlook"

# module -> public name -> parameters (a function), field names (a
# dataclass), or None (an exception or a constant). A parameter is written as
# a call may give it: ``name`` when it is required, ``name=`` when it has a
# default, and with a leading ``*`` or a trailing ``/`` if it ever stops being
# passable both by position and by name.
PUBLIC = {
    "store": {
        "StoreError": None,
        "archive_root": (),
        "id_hash": ("internet_message_id",),
        "stable": ("payload",),
        "load_hits": ("root=",),
        "load_messages": ("root=",),
        "newest_hit": ("root=", "mailbox="),
    },
    "classify": {
        "Mail": (
            "mid",
            "received",
            "sender",
            "sender_name",
            "recipients",
            "subject",
            "text",
            "conversation_id",
            "has_attachments",
            "full",
            "mailbox",
        ),
        "Facts": ("role", "outcome", "extra"),
        "view": ("payload", "mailbox="),
        "classify": ("mail",),
        "is_auto_reply": ("subject",),
        "is_reply": ("subject",),
        "thread_subject": ("subject",),
    },
    "detail": {
        "detail": ("message",),
        "details_by_id": ("root=",),
        "body_text": ("message",),
    },
    "config": {
        "Settings": (
            "root",
            "source",
            "mailbox",
            "own_addresses",
            "system_senders",
            "decision_tag",
            "archive_dir",
            "captured_dir",
            "scratch_dir",
            "timezone",
            "render_label",
            "profile",
            "origins",
        ),
        "ConfigError": None,
        "KEYS": None,
        "DEFAULTS": None,
        "load": ("start=",),
        "settings": (),
        "mailbox": (),
        "archive_dir": (),
        "captured_dir": (),
        "scratch_dir": (),
        "zone": (),
        "zone_label": ("when=",),
    },
}

KEYS = (
    "mailbox",
    "own_addresses",
    "system_senders",
    "decision_tag",
    "archive_dir",
    "captured_dir",
    "scratch_dir",
    "timezone",
    "render_label",
    "profile",
)
DEFAULTS = {
    "archive_dir": "data/outlook",
    "captured_dir": "temp/outlook/captured",
    "scratch_dir": "temp/outlook",
    "timezone": "America/Los_Angeles",
    "render_label": "Message",
}
ROLES = {"arrival", "followup", "ours", "decision", "system"}


def module(name):
    return importlib.import_module(f"outlooks.{name}")


def written(parameter):
    """A parameter as :data:`PUBLIC` writes it."""
    kind = parameter.kind
    mark = {
        kind.POSITIONAL_ONLY: "{}/",
        kind.POSITIONAL_OR_KEYWORD: "{}",
        kind.VAR_POSITIONAL: "*{}",
        kind.KEYWORD_ONLY: "*{}",
        kind.VAR_KEYWORD: "**{}",
    }[kind]
    default = "" if parameter.default is parameter.empty else "="
    return mark.format(parameter.name + default)


@pytest.mark.parametrize("name", sorted(PUBLIC))
def test_all_names_exactly_the_public_names(name):
    assert sorted(module(name).__all__) == sorted(PUBLIC[name])


@pytest.mark.parametrize(
    "name,attr", [(m, a) for m, names in PUBLIC.items() for a in names]
)
def test_public_name_keeps_its_parameters(name, attr):
    found = getattr(module(name), attr)
    want = PUBLIC[name][attr]
    if want is None:
        return
    if dataclasses.is_dataclass(found):
        assert tuple(f.name for f in dataclasses.fields(found)) == want
    else:
        parameters = inspect.signature(found).parameters.values()
        assert tuple(written(p) for p in parameters) == want


def test_a_parameter_is_written_with_its_default_and_its_kind():
    def f(a, /, b, c=None, *, d, e=1):
        return a, b, c, d, e

    parameters = inspect.signature(f).parameters.values()
    assert tuple(written(p) for p in parameters) == ("a/", "b", "c=", "*d", "*e=")


def test_every_other_module_says_it_is_internal():
    for info in pkgutil.iter_modules(outlooks.__path__):
        if info.ispkg or info.name in PUBLIC:
            continue
        found = module(info.name)
        assert "Internal: not part of the Python API" in (found.__doc__ or ""), (
            info.name
        )
        assert not hasattr(found, "__all__"), info.name


def test_settings_keys_and_defaults_are_pinned():
    from outlooks import config

    assert config.KEYS == KEYS
    assert config.DEFAULTS == DEFAULTS


def test_mail_carries_what_a_refining_wrapper_reads():
    from outlooks import classify as cl

    mail = cl.view(json.loads((FIXTURES / "system-notice.json").read_text("utf-8")))
    assert mail.sender == "notices@system.example.org"
    assert mail.recipients == ("desk@example.org",)
    assert mail.subject == "New request received"
    assert "Title: Pruning Apple Trees in Late Winter" in mail.text
    assert cl.classify(mail).role in ROLES


# --- the README's examples --------------------------------------------------------


def readme_examples():
    """The python blocks of the README's "Python API" section, in order."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("\n## Python API\n", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"```python\n(.*?)```", section, re.S)


def notice(text):
    payload = json.loads((FIXTURES / "system-notice.json").read_text("utf-8"))
    return {**payload, "body": {"contentType": "text", "content": text}}


def test_readme_names_every_public_name():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("\n## Python API\n", 1)[1].split("\n## ", 1)[0]
    for name, names in PUBLIC.items():
        assert f"`outlooks.{name}`" in section
        for attr in names:
            assert f"`{attr}`" in section, f"{name}.{attr}"


def test_readme_reading_example_runs(tmp_path, capsys):
    from outlooks import store

    reading, _ = readme_examples()
    inquiry = json.loads((FIXTURES / "inquiry.json").read_text("utf-8"))
    store.save_hits(json.loads((FIXTURES / "hits-page.json").read_text("utf-8")))
    store.save_message(inquiry)
    exec(compile(reading, "README.md", "exec"), {})
    out = capsys.readouterr().out
    assert len(out.splitlines()) == len(store.load_hits())
    assert inquiry["subject"] in out


def test_readme_refining_example_runs():
    _, refining = readme_examples()
    scope: dict[str, object] = {}
    exec(compile(refining, "README.md", "exec"), scope)
    facts = scope["facts"]
    assert callable(facts)
    opened = notice("Ticket #12 was opened by R. Example.")
    assert facts(opened) == ("system-opened", 12)
    assert facts(notice("Ticket #12 was closed.")) == ("system-other", None)
    inquiry = json.loads((FIXTURES / "inquiry.json").read_text("utf-8"))
    assert facts(inquiry) == ("arrival", None)


# --- the commands -----------------------------------------------------------------

# A command's name is part of the interface: a repo's skill, its CI, and its
# operator's habits all name it.
COMMANDS = {
    "audit",
    "check",
    "config",
    "coverage",
    "doc",
    "doctor",
    "hash",
    "hook",
    "import",
    "install",
    "lookup-reset",
    "permissions",
    "render",
    "save",
    "senders",
    "skill",
    "split",
    "totals",
    "windows",
    "worklist",
}
WINDOWS = {"init", "fill", "split", "batches", "remaining"}


def commands():
    from typer.core import TyperGroup
    from typer.main import get_command

    from outlooks.cli import app

    group = get_command(app)
    assert isinstance(group, TyperGroup)
    return group.commands


def test_command_names_are_pinned():
    found = commands()
    assert set(found) == COMMANDS
    windows = found["windows"]
    assert set(getattr(windows, "commands", {})) == WINDOWS


def test_readme_lists_every_command():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("\n## Commands\n", 1)[1].split("\n## ", 1)[0]
    for name in COMMANDS:
        assert f"`outlooks {name}" in section, name
    assert "`outlooks windows init/fill/split/batches/remaining`" in section


def test_readme_fixture_pins_every_setting_the_suite_pins():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("\n## Testing against it\n", 1)[1].split("\n## ", 1)[0]
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    pinned = set(re.findall(r'"(OUTLOOKS_[A-Z_]+)"', conftest))
    assert pinned == set(re.findall(r'"(OUTLOOKS_[A-Z_]+)"', section))
    assert {name.removeprefix("OUTLOOKS_").lower() for name in pinned} == set(KEYS)
