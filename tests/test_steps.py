"""The skill's step names, pinned per mode.

A repo's own skill cites these steps by name ("the sweep's ``classify``
step"), so adding, removing, renaming, or reordering one is a deliberate edit
to this file, and a changelog entry under *Changed*.
"""

import re
from importlib.resources import files

import pytest

from outlooks.host import MODES

# A step starts at a numbered heading that ends in (step `name`), or at a
# numbered list item that opens with **Step `name`.**
NAME = r"`([a-z][a-z-]*)`"
STEP = re.compile(
    rf"^(?:## (\d+)\. .*\(step {NAME}\)|(\d+)\. \*\*Step {NAME}\.\*\*)", re.M
)

STEPS = {
    "sweep": [
        (0, "setup"),
        (1, "sweep"),
        (2, "classify"),
        (3, "match"),
        (4, "worklist"),
        (5, "file"),
        (6, "ledger"),
        (7, "report"),
    ],
    "lookup": [
        (0, "setup"),
        (1, "resolve"),
        (2, "pull"),
        (3, "gaps"),
        (4, "timeline"),
        # Running a lookup through subagents
        (1, "search"),
        (2, "split"),
        (3, "read"),
        (4, "decide"),
        (5, "keep"),
    ],
    "window": [
        (0, "setup"),
        # Pull a window
        (1, "pull-gaps"),
        (2, "pull-size"),
        (3, "pull-page"),
        (4, "pull-import"),
        (5, "pull-report"),
        # Collect a whole range's hits
        (1, "collect-hook"),
        (2, "collect-init"),
        (3, "collect-size"),
        (4, "collect-split"),
        (5, "collect-page"),
        (6, "collect-import"),
        (7, "collect-prove"),
        # Read a whole range in full
        (1, "read-repull"),
        (2, "read-worklist"),
        (3, "read-launch"),
        (4, "read-import"),
        (5, "read-audit"),
    ],
}


def body(mode):
    return (files("outlooks.prompts") / f"skills/{mode}/SKILL.md").read_text("utf-8")


def steps(text):
    return [(int(m[1] or m[3]), m[2] or m[4]) for m in STEP.finditer(text)]


def test_every_mode_is_pinned():
    assert sorted(STEPS) == sorted(MODES)


@pytest.mark.parametrize("mode", MODES)
def test_step_names_and_numbers_are_as_pinned(mode):
    assert steps(body(mode)) == STEPS[mode]


@pytest.mark.parametrize("mode", MODES)
def test_a_name_is_used_once_in_its_mode(mode):
    names = [name for _, name in STEPS[mode]]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("mode", MODES)
def test_no_numbered_heading_is_left_unnamed(mode):
    numbered = re.findall(r"^## \d+\. .*$", body(mode), re.M)
    assert all(STEP.match(line) for line in numbered), numbered


@pytest.mark.parametrize("mode", MODES)
def test_the_mode_says_what_the_names_are_for(mode):
    assert "cites it by that name" in body(mode)


def test_the_profile_template_says_to_cite_steps_by_name():
    template = files("outlooks.prompts") / "references/profile-template.md"
    text = template.read_text("utf-8")
    assert "cite the step by its name, not its number" in text
    # Its own references to a step are by name.
    assert not re.search(r"\bstep \d", text)
