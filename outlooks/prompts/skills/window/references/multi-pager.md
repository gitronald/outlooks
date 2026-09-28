# Multi-window pager

Work from the repo root your launch prompt names.

Your batch file lists one window per line: `AFTER BEFORE SIZE` (SIZE is the
expected count, a hint only). Run `{cli} doc window/pager-brief` once first
and read it. Then, for each line in order, page that window exactly as the
brief describes, using AFTER and BEFORE as its two bounds — every window
here has both. Finish one window before starting the next.

Return only, per window: AFTER, the `totalResultCount` from its offset-0
page, and the offsets you called. Write nothing.
