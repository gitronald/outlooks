#!/usr/bin/env bash
# Capture hook for the Microsoft 365 connector (PostToolUse on
# outlook_email_search and read_resource), written by `outlooks hook --apply`.
# Writes the hook payload verbatim to {YYYYMMDDTHHMMSS}-{pid}.json under
# $OUTLOOKS_CAPTURED_DIR, else under the captured_dir of [tool.outlooks] as it
# was when this script was written: the copy of the connector's output that
# `outlooks import` files. `outlooks hook` reports the script stale once
# captured_dir changes, and `outlooks hook --apply` rewrites it.
#
# A result too large for the context reaches the hook only as a note naming the
# file Claude Code saved it to, under ~/.claude/projects/, which the repo does
# not keep. Copy that file beside the capture as {same name}.saved.txt, so the
# capture never depends on it: without the copy, a capture imported after the
# file is gone has lost its output.
#
# Always exits 0: a capture problem must not fail the connector call.
d="${OUTLOOKS_CAPTURED_DIR:-}"
[ -n "$d" ] || d=@CAPTURED_DIR@
mkdir -p "$d"
out="$d/$(date +%Y%m%dT%H%M%S)-$$.json"
cat > "$out"

# Only a string tool_response is a spill note; a hit's summary quoting the
# phrase sits in a list of blocks and never matches.
saved=$(grep -oE \
  '"tool_response": ?"[^"]*Output has been saved to [^ "\\]+\.txt' "$out" |
  sed -E 's/.*Output has been saved to //')
# Claude Code saves those files under its projects directory; a note naming a
# file anywhere else is not one of its own, and nothing is copied for it.
root="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects"
real=$(cd "$root" 2>/dev/null && pwd -P)
case "$saved" in
  */../*) saved="" ;;
  "$root"/*) ;;
  "${real:-$root}"/*) ;;
  *) saved="" ;;
esac
if [ -n "$saved" ] && [ -f "$saved" ]; then
  cp "$saved" "${out%.json}.saved.txt"
fi
exit 0
