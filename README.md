# talking-head-reels

Turns talking-head videos recorded on your phone into finished Instagram Reels, on your own Mac and for
free. You record and approve; the pipeline transcribes, picks the best 30-60 s clips, cuts dead air,
fillers and swears, levels the audio, burns in animated captions and writes the post caption. Posting
stays manual (Meta Business Suite or the Instagram app).

```
phone ──Syncthing──▶ inbox/ ──watcher──▶ transcribe ─▶ pick clips ─▶ cut + de-silence ─▶ -14 LUFS
                                         (whisper)     (claude -p)   (auto-editor/ffmpeg)
                    ─▶ cut swears ─▶ animated captions ─▶ ready/<reel>.mp4 + .txt ──Syncthing──▶ phone
                                     (Remotion, ASS fallback)       notes ─▶ notes/
                                                                    source ─▶ archive/
```

## What you need

* A Mac with Apple Silicon (transcription runs on the GPU via Metal) and ~5 GB free disk.
* [Homebrew](https://brew.sh), [uv](https://docs.astral.sh/uv/) (`brew install uv`) and Node.js 18+ (`brew install node`).
* [Claude Code](https://claude.com/claude-code), installed and logged in to a Claude plan. It picks the
  clips and writes the captions; the pipeline calls it as `~/.local/bin/claude` (set `REELS_CLAUDE` if
  yours lives elsewhere).
* An Android phone for recording. Any phone works if you get the videos into `inbox/` some other way
  (AirDrop, iCloud Drive, a cable).

## One-time setup

### 1. Mac

Everything below is free and open source. Remotion is free for individuals and companies of up to
3 people; a bigger company would need a paid Remotion licence.

```bash
git clone https://github.com/P34chsauce/talking-head-reels.git
cd talking-head-reels
bin/setup                       # brew tools, venvs, models (~3 GB in models/), Remotion; safe to re-run
brew services start syncthing   # runs at login; web UI at http://127.0.0.1:8384
```

`claude -p` uses your Claude subscription login. The pipeline strips `ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_BASE_URL` before calling it, so it never bills at API rates.

### 2. Make it yours

Everything about *your* channel lives in a few small files. The defaults are in `config/`. Put your own
versions in `private/` (create the folder; git ignores it), and they win:

| File | What it does | How `private/` combines |
|---|---|---|
| `channel.md` | Who you are, what the channel is about, voice, hashtags, house rules. Claude reads it with every transcript. | replaces |
| `settings.json` | `notes_dir` (where session notes go), `project_repo` + `project_branch` (the git repo `talking-points` reads), `whisper_prompt` (a punctuated opening line; include names Whisper would misspell) | per key |
| `banned-phrases.txt` | Regexes no caption may contain (`[copy]`), plus claims you may not say on camera either (`[claims]`) | replaces |
| `swears.txt` | Words cut out of the audio | replaces |
| `style.json` | Caption font, size, colours, position, emojis per clip, `swearMode` | per key |

```bash
mkdir -p private && cp config/channel.md private/channel.md   # then edit it
```

### 3. Phone (Android)

1. **Camera:** install **Open Camera** (free, open source; Play Store or F-Droid). In Settings → *More
   camera controls* → *Save location*, choose a new folder `Movies/Reels-Inbox`. Record in portrait (9:16),
   1080p, 30 fps. Only videos made with Open Camera reach the pipeline, so your personal clips stay private.
   *(Prefer the built-in camera? Then move each take into `Movies/Reels-Inbox` with your file manager.)*
2. **Syncthing:** install **Syncthing-Fork** (F-Droid or its GitHub releases; the original Syncthing
   Android app was discontinued). Let it run in the background and exempt it from battery optimisation.
3. **Pair the devices:** in the Mac web UI (http://127.0.0.1:8384) → *Add Remote Device* → scan the phone's
   device ID QR code (Syncthing-Fork → menu → *Show device ID*). Accept on both sides.

### 4. Share two folders

| Folder | Phone path | Phone type | Mac path | Mac type |
|---|---|---|---|---|
| **Reels Inbox** | `Movies/Reels-Inbox` | **Send Only** | `<repo>/inbox` | **Receive Only** |
| **Reels Ready** | `Movies/Reels-Ready` | Send & Receive | `<repo>/ready` | Send & Receive |

* Inbox is one-way: the phone keeps its originals, and error logs on the Mac never reach the phone.
  When the pipeline moves a finished source to `archive/`, Syncthing shows the Mac folder as
  "locally changed". That's expected. **Don't** press *Revert Local Changes* (it would re-download every
  archived recording; the pipeline would recognise them and archive them again, but it's wasted transfer).
* Ready is two-way so deleting a reject on the phone also deletes it on the Mac.

### 5. Start the watcher

```bash
bin/watcher-ctl start      # install + start the LaunchAgent (runs at every login from now on)
bin/watcher-ctl status     # loaded? pid? inbox / failures / ready
bin/watcher-ctl logs       # follow logs/watcher.log  (Ctrl-C to stop following)
bin/watcher-ctl stop       # stop it and remove it from login
```

Under the hood that's `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.reels.watcher.plist`
and `launchctl bootout gui/$(id -u)/local.reels.watcher`.

## Weekly routine

1. **Monday: get prompts (optional).** If you build something in git, set `project_repo` in
   `private/settings.json`. `bin/reels talking-points` then reads the last 7 days of commits (read-only),
   plus what past recordings covered, and drafts 5-7 riff prompts about how the week *felt*. They're saved
   to `<notes_dir>/talking-points/<date>.md`.
2. **Record.** One or more takes in Open Camera, a few minutes each. Talk naturally; pauses, ums and swears
   are handled for you. Each recording yields up to 5 reels of 30-60 s.
3. **Wait.** The take syncs to `inbox/` and the watcher starts once the file stops growing. Reels arrive in
   `Movies/Reels-Ready` on the phone, usually within 10-20 minutes.
4. **Approve.** Watch each reel on the phone. Delete rejects (the `.mp4` and its `.txt`).
5. **Schedule.** In Meta Business Suite, schedule one reel per day for the week. Paste the caption from the
   matching `.txt` (caption + hashtags). Delete scheduled reels from `Reels-Ready` so the folder stays your backlog.
6. **Glance at failures.** `bin/watcher-ctl status`. A failed take stays in `inbox/` with
   `<name>.error.log` next to it on the Mac.

Session notes (summary, ideas, quotable lines with timestamps, clips, material left over) land in
`notes_dir` (default `notes/`, ignored by git) as `<date>_<slug>.md`. Point it at an Obsidian vault if you
use one.

## What the pipeline does to each take

| Step | Tool | Notes |
|---|---|---|
| Wait for full sync | size/mtime settle + `ffprobe` | Syncthing temp files (`.syncthing.*.tmp`) are ignored |
| Transcribe | whisper.cpp large-v3-turbo (Metal) for the text, WhisperX wav2vec2 forced alignment for word timing | |
| Pick clips, write notes + post copy | `claude -p` + `.claude/skills/make-reels` | JSON schema; validator re-asks with concrete fixes, keeps the valid clips after 3 rounds; leading "so/um/okay" trimmed |
| Cut + remove dead air | auto-editor timeline → ffmpeg | one encode; word timings remapped exactly |
| Swears, "um"/"uh", dropped lines | cut out of video + audio, cut edges snapped to the quietest audio, never into the next word; Claude drops retakes, asides and tangents (`cut_lines`) and fixes mistranscribed caption words | `swearMode: "mute"` in `style.json` mutes instead |
| Loudness | voice compressor, measured exact gain, peak limiter, re-measured | -14 LUFS integrated (±0.2), under -1.5 dBTP |
| Captions | Remotion (word highlight in `highlightColor`, 0-2 emojis) | falls back to ASS/libass automatically (no emojis) |
| Copy check | `banned-phrases.txt` | gates every caption and the spoken words of every clip |
| Publish | `ready/`, `notes_dir`, `archive/` | content-hash ledger so nothing is done twice |

## Commands

```bash
bin/reels process                 # sweep inbox/ now (what the watcher runs)
bin/reels process inbox/X.mp4     # one file, skipping the sync wait and retry backoff
bin/reels retry                   # clear every error log in inbox/ and try again
bin/reels reprocess archive/X.mp4 [--retranscribe]   # run a finished take again; old reels kept in work/<job>/previous/
bin/reels status
bin/reels check-copy ready/*.txt  # banned-phrase check; exit 1 on any hit
bin/reels talking-points [--days 7] [--dry-run]
tests/fixtures/make_test_clip.sh /tmp/test.mp4   # synthetic 2-minute take (say + ffmpeg)
uv run pytest                     # unit tests
.venv/bin/python tests/bench_alignment.py AUDIO.wav a=words.json …   # compare word-timing accuracy
```

## Rules baked in

* **Copy:** no therapeutic or medical claims ("treats", "cures", "reduces anxiety", …) out of the box.
  Add your own wording rules in `private/banned-phrases.txt` and describe them in `private/channel.md`. `[copy]` rules apply to post copy; `[claims]` rules also
  apply to what you say in a clip (talking about your own mental health is fine; claims are not).
* **Swears:** `swears.txt` (stems like `fuck` match `fucking`; `=word` matches the whole word only).
* **Heavy jobs:** if `~/.claude/scripts/heavy-queue.sh` (or `$REELS_HEAVY_QUEUE`) exists, transcription
  and renders go through it so they never run alongside other heavy jobs. Otherwise they just run.
* **Failures:** the source stays in `inbox/` with `<name>.error.log`. Short-lived failures (usage limit,
  billing guard, network) retry automatically after 1h, 2h, 4h, 8h; after 5 attempts, or on a permanent
  failure (e.g. under 30 s of speech), it waits for `bin/reels retry`.
* **Media never enters git.** Nor do `private/` and `notes/`.

## Troubleshooting

* **Nothing happening?** `bin/watcher-ctl status`, then `bin/watcher-ctl logs`.
* **A take failed:** read `inbox/<name>.error.log`. Fix the cause, then `bin/reels retry`.
* **Remotion failed:** the clip still ships with ASS captions (no emojis); the log says
  `Remotion failed, using ASS fallback`.
* **Testing by hand from inside a Claude Code session:** the pipeline strips Claude Code's session variables
  before calling `claude -p`, so it behaves exactly as it does under launchd.

## Tuning

`style.json` (in `private/` for your own): font size, colours (`highlightColor` is the word being spoken),
words per phrase, caption height (`captionCenterY`), emojis per clip, `swearMode`. Silence thresholds and clip
lengths live in `lib/reels/config.py`.
