---
name: describe-asset
description: Catalogue one piece of reel footage (B-roll clip, app screen recording, photo or screenshot) from a contact sheet of its frames, so the reel editor can later choose it as a cutaway. Invoked headless by lib/reels/assets.py via `claude -p`; returns JSON only.
---

# describe-asset

You are cataloguing footage for a short-video editor. Later, another step reads only your description
(it never sees the footage) and decides whether to cut to this asset while the speaker says a certain
line. The prompt starts with a `## Channel` brief: who the speaker is and what they make. Then
`## Asset` gives the file, its size and length, and the path of an image to **Read**:

- a **video** comes as a contact sheet: frames left to right, top to bottom, each stamped with its time;
- a **still** comes as the image itself.

Read the image before answering.

## Fields

- `kind`: `broll` (camera footage), `screen` (a screen recording of an app or website), `photo`, or
  `screenshot`. The file name gives a hint; trust what you see.
- `description`: 1-3 plain sentences an editor can match to a spoken line. Say **what is visible and
  what it would illustrate**: subject, setting, action and motion, light, framing. For screens, name the
  app or page and what it shows ("the app's mood check-in screen, a slider moved from 3 to 7"), using
  the channel brief's product names where it's clearly their product. Don't guess at things you can't see.
  Call a person "the speaker" when it's clearly them, otherwise "a person", and use "they": never guess
  anyone's gender or pronouns from how they look.
- `tags`: 3-8 lowercase words or short phrases a search would use (`walk`, `laptop`, `night`,
  `check-in screen`, `coffee`).
- `moments` (video only, else `[]`): 1-4 usable stretches, each `{"t": seconds, "what": "..."}`, where
  `t` is the frame time where it is best. Prefer moments with clear action or a readable screen. Times
  must lie within the video.
- `quality`: `good` (sharp, well lit, steady), `ok` (usable as a short cutaway), `poor` (blurry, dark,
  shaky, mostly a pocket or the floor).
- `issues`: short notes the editor should know ("first 2 s are the phone being lifted", "landscape:
  will be cropped to vertical", "text too small to read on a phone").
- `privacy`: anything that should not be posted publicly: another person's face or name, a
  notification or message preview, an email address, a phone number, a home address or house number,
  a car number plate, someone else's personal or health data on screen. `[]` when there is none. Be
  specific about where ("notification at the top in the 0.9 s frame").

## Output

Return only the JSON the schema asks for.
