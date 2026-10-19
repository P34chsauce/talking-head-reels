---
name: revise-reel
description: Apply the founder's timestamped review comments to one finished reel's edit list (clip range, cut lines, cutaways, emojis, caption fixes, post copy) and return the revised clip. Invoked headless by lib/reels/review.py via `claude -p`; returns JSON only.
---

# revise-reel

The founder watched a finished reel and left comments pinned to moments in it ("at 0:09: swap this for
the garden", "at 0:31: caption should say commits", "end a beat earlier"). You get:

- `## The reel now`: the clip's current edit list as JSON. The fields mean exactly what they mean in the
  make-reels skill: `first_line`/`last_line`/`cut_lines` (which transcript lines the viewer hears),
  `visuals` (cutaways: `from_line`, `to_line`, `asset`, `at`, `mode` `full`/`pip`), `emojis`,
  `caption_fixes`, `caption`, `hashtags`.
- Where each kept line plays in the reel now (when available) and which cutaways are on screen when.
  Use these to turn a comment's time into the line or cutaway it's about.
- The transcript lines around the clip (lines outside it can be brought in by moving `first_line` or
  `last_line`).
- The footage you can cut to, with ids.
- The comments, and the channel brief.

## Rules

- **Do exactly what the comments ask, and nothing else.** Every field no comment touches stays
  byte-for-byte the same. Don't "improve" the rest of the reel.
- A comment's time points at what was on screen then: the cutaway covering that time, or the line being
  spoken. "This" / "here" means that.
- **Swap footage:** pick the asset that best matches the comment ("the garden", "something outside",
  "the app instead"); set `at` to a listed moment that fits. **Shorter / longer / earlier / later:**
  move `from_line`/`to_line` by whole lines. **Remove:** drop the cutaway. **Add:** a new cutaway on the
  line at that time.
- **Caption words on screen** (the burned-in captions): add a `caption_fixes` entry for that line
  (`wrong` exactly as transcribed, `right` 1-3 words). **Post copy** (the text under the reel): edit
  `caption` or `hashtags`.
- **Trim the reel:** move `first_line`/`last_line`, or add/remove `cut_lines`. The reel must stay
  30-60 s; the validator tells you if it doesn't.
- All make-reels rules still hold: no medical or therapeutic claims, no engagement bait, one ask, a
  cutaway 1-7 s, footage long enough from `at`.
- If a comment can't be done (it asks for footage that doesn't exist, or would break a rule), leave that
  part unchanged and say so in `summary`.

## Output

The full revised clip, with every field, plus `summary`: one or two plain sentences on what you
changed, one per comment, in the order of the comments ("0:09: swapped the treadmill for the garden
walk. 0:31: fixed 'comets' to 'commits'.").
