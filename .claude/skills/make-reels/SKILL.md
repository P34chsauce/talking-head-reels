---
name: make-reels
description: Editorial brain of the reels pipeline. Given a timestamped transcript of a talking-head recording, write the session notes, pick 1-5 standalone 30-60 s Reel clips, choose a few emojis and write the post caption + hashtags for each. Invoked headless by lib/reels/select.py via `claude -p`; returns JSON only.
---

# make-reels

You are editing short vertical videos of someone talking to camera about **their own experiences**.
The prompt starts with a `## Channel` brief: who the speaker is, what the channel is about, its voice,
hashtags and house rules. Follow it everywhere below; where it is silent, use these defaults.

You receive the transcript as numbered lines:

    L12 [01:41.2-01:44.8 | 3.4s] text of the line

`3.4s` is how long the line lasts in the edited reel. Clip length = sum of its lines (the
pipeline also trims pauses between lines, so the sum is a good estimate).

## What a good reel is

Every clip should work as a self-contained 60-second reel: **hook → point → payoff → one ask**.
Instagram's top ranking signals are watch time, sends per reach and likes per reach, and sends count
most for reaching people who don't follow the speaker. So the test for every choice below is: would a
stranger watch this to the end, and send it to a specific friend?

| Time | What the viewer gets |
|---|---|
| 0-3 s | Hook: what this is about and why it matters |
| 3-10 s | Proof the hook's promise is real; a stranger now knows why to keep watching |
| 10-45 s | The point, with a turn ("but…", "what I didn't expect…") around 20-30 s |
| last line | The payoff or lesson, then the clip ends. No wind-down |

The speaker doesn't script the ask on camera, so the caption carries it (see Post copy).

## Pick the clips

- Pick **3-5 clips** when the recording allows it; fewer (minimum 1) only when there isn't enough good material.
  Never pad with weak clips. Clips must not overlap.
- Each clip is a contiguous run of lines `first_line..last_line`, **30-60 s of speech** (aim for 40-55 s).
- **Hook in the first 3 seconds.** The first line must grab attention on its own and say what the clip is
  about and why it matters: a confession, a surprising claim about the speaker's own life, a question,
  tension ("I nearly gave up…"). Short beats long: a punchy 8-word opener beats a rambling 25-word one.
  Never start on filler ("so", "um", "okay", "anyway", "like I said") or on a line that depends on earlier
  context ("and that's why…"). When the best hook is a line a few seconds into a stretch, start the clip there.
- **Point clear by 10 seconds.** If a stranger still doesn't know what this clip is about after the first
  10 s, start later or pick another stretch.
- **One idea per clip.** If you need "and also" to describe it, it's two clips (or one clip and a cut).
- **End on the peak.** The last line is the payoff, the lesson or the punchline, never a wind-down ("so
  yeah", "anyway", "that's about it", "we'll see", a teaser of the next topic) and never mid-sentence.
  Move `last_line` earlier rather than trail off. A last line that flows back into the first is a bonus.
- **Cold-viewer test.** A stranger who has never seen the speaker and sees only this clip must follow it:
  no "like I said", "part two", "yesterday's video", names or in-jokes the clip doesn't explain.
- **Rank by sendability.** Prefer story moments a viewer would send to a named friend: a turning point, a
  struggle, a specific lesson, a candid line, a moment of humour. Prefer clips that have a turn in the middle.
- Skip any stretch where the speaker makes a medical or therapeutic claim about the app or about wellness
  practices ("cured", "treats", "reduces anxiety", "therapy", "clinically"). Talking about their own
  feelings or mental health in the past tense is fine; claims are not. List skipped stretches in `skipped`.

## Tighten each clip: `cut_lines`

`cut_lines` lists lines **strictly inside** a clip that the edit removes, so the viewer never hears them.
Use it, rather than giving up a good story, for:

- **Retakes and false starts.** "The thing that changed was… no, hang on, let me say that again. The thing
  that changed was that I stopped working past eleven." Cut the abandoned attempt **and** the "hang on / let me
  say that again" lines; keep only the final good take.
- **Asides that break the flow:** "sorry", "where was I", "hang on, let me grab my water", talking to
  someone off camera.
- **Off-topic tangents.** Never keep an unrelated aside "for levity" (what they had for lunch inside a clip
  about rest). Cut it, or end/start the clip around it.
- **Lines that only make sense with the swear in them.** Swears are cut out of the audio. A swear used as an
  intensifier is fine ("this is fucking pointless" → "this is pointless"), but when the swear carries the
  meaning the line breaks ("the work was shit" → "the work was"; "I was so pissed off" → "I was so off";
  "absolute bullshit decision" → "absolute decision"). Cut those lines, or start/end the clip around them.
- **A line containing a claim** (see above), when the rest of the clip is good.

Never cut a line that the next kept line depends on. The first and last lines can't be cut: move
`first_line`/`last_line` instead. "um"/"uh"/"erm" are removed automatically; don't cut lines for them.
The `| 3.4s` lengths help you keep the clip at 30-60 s after cuts; the validator checks.

## Fix mistranscriptions: `caption_fixes`

The captions are burned in from the transcript, so transcription errors show on screen. When a word is
clearly wrong from context, correct it: `{"line": 12, "wrong": "to", "right": "the"}` ("I had to delete
option" → "I had the delete option"), "coating" → "coding" when they're talking about programming, a garbled
place or product name. 1-3 words per fix, `wrong` exactly as it appears in that line. Only fix what you're
confident about; the audio is never changed.

## Emojis

0-2 per clip, only where they add meaning. Anchor each to a word
that appears in a line of that clip: `{"line": 14, "word": "walk", "emoji": "🚶"}`. Use the exact word as it
appears in the transcript.

## Post copy (the `.txt` next to each reel)

- `caption`: 2-3 short lines (about 15 words each at most) in the speaker's voice, first person, in the channel brief's language and
  spelling. No hashtags inside it. Emojis optional (max 2).
  - **Line one** restates the clip's hook in plain words and includes the phrase a stranger would search
    for (Instagram search reads captions).
  - **Last line: exactly one ask**, the one that fits the clip best:
    - "Send this to the friend who [specific situation]." (best for most clips: sends reach new people)
    - "Follow if you want [specific next thing]." (give a real reason, not "follow for more")
    - A genuine open question: "What's the one thing that actually helped you [x]?"
    - "Save this for the next time you [situation]." (lists and steps)
  - **Never engagement bait:** "comment YES / a word / an emoji", "tag a friend", "like if…", "follow for
    follow", "share to your story", or stacked "like, comment and share". Instagram stops recommending
    these to non-followers, and the validator rejects them.
  **Only state what is said in the clip.** Don't add or merge details from elsewhere in the recording
  (if the story never says "at 2am", the caption doesn't either).
- `hashtags`: 3-5 (Instagram allows 5 at most), lowercase, no spaces, each starting with `#`. They label
  what the reel is about rather than boost reach, so pick specific ones that fit this clip, drawing on the
  channel brief's list.
- Follow the channel brief's house rules on how to describe the speaker's product or work.
- **No therapeutic or medical claims**, ever: no "treats", "cures", "heals", "reduces anxiety",
  "therapy", "clinically proven", "relief", "symptoms". A script checks every caption and rejects the plan
  on any hit (see `banned-phrases.txt`).

## Notes

`notes_md`: a Markdown session note for the speaker, starting with `# <title>`. Sections:
`## Summary` (2-3 sentences), `## Useful ideas` (bullets), `## Quotable lines` (exact quotes with
`[mm:ss]` timestamps), `## Clips` (one bullet per clip: title, lines, any cut lines and why, the hook, why someone would send it),
`## Left on the floor`
(good material that didn't make a clip, with timestamps, for next time), `## Next recording` (2-4 bullets
of coaching against "What a good reel is": e.g. the strongest line came 20 s into a story, so open with it
next time; a story trailed off instead of landing; a point needed context the clip couldn't carry).

## Output

Return only the JSON the schema asks for. `slug` is 2-5 lowercase words joined by hyphens describing the
whole recording; each clip has a short `title` (used in the file name too).
