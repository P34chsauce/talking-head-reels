#!/bin/bash
# Build a synthetic ~3 min vertical talking-head stand-in: `say` narration with deliberate
# dead air and two swear words, over a 1080x1920 background that shows the source timecode
# (so cuts are visible in the finished reel).
#   tests/fixtures/make_test_clip.sh <out.mp4>
set -euo pipefail
OUT="${1:?usage: make_test_clip.sh <out.mp4>}"
FF=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/script.txt" <<'EOF'
So I want to tell you about the week I nearly gave up on building this app. [[slnc 1800]]
It was a Tuesday night, I had been coding for about six hours, and nothing was working. Every test I fixed broke two more. [[slnc 600]] And I remember sitting there thinking, who am I kidding? I'm one person trying to build something that big companies have whole teams for. [[slnc 700]] But then I went for a walk. No phone, no podcast, just me and the cold air. And somewhere around the second lap of the park, I realised the problem wasn't the code. The problem was that I'd stopped doing the exact things my own app tells people to do. I wasn't sleeping, I wasn't eating properly, I wasn't moving. [[slnc 900]] I was building a holistic wellness app while completely ignoring my own wellbeing. [[slnc 2500]]
Um, [[slnc 1200]] okay. [[slnc 800]]
Here's the thing nobody tells you about building in public. You feel like you have to show the wins. The launch, the downloads, the nice screenshots. [[slnc 500]] But honestly, the bits that people message me about are the messy bits. The time I deleted a whole week of work by accident. The time I shipped a bug that showed everyone the wrong streak. [[slnc 600]] That was a fucking disaster, by the way. [[slnc 700]] People don't connect with perfect. They connect with real. So that's what I'm going to keep doing here, showing the real stuff, even when it's a bit embarrassing. [[slnc 2000]]
One more thing before I go. [[slnc 600]]
I used to think rest was something you earned after the work was done. Like a reward. [[slnc 500]] Now I think rest is part of the work. When I take a proper evening off, I come back and solve in twenty minutes what I was stuck on for three hours. [[slnc 600]] It sounds obvious when I say it out loud, but it took me a really long time, and a lot of late nights feeling like shit, to actually believe it. [[slnc 700]] So if you're grinding on something right now, maybe close the laptop tonight. The problem will still be there tomorrow, and you'll be better at solving it. [[slnc 1500]]
EOF

say -v Daniel -r 175 -o "$TMP/voice.aiff" -f "$TMP/script.txt"
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$TMP/voice.aiff")
FONT="$(cd "$(dirname "$0")/../.." && pwd)/assets/fonts/AtkinsonHyperlegible-Bold.ttf"

"$FF" -hide_banner -loglevel error -y \
  -f lavfi -i "color=c=0xFFD5C8:s=1080x1920:r=30:d=$DUR" \
  -i "$TMP/voice.aiff" \
  -vf "drawbox=x='540+300*sin(t)-120':y=760:w=240:h=400:color=0xFF6B4A@0.9:t=fill,drawtext=fontfile=$FONT:text='src %{pts\:hms}':x=60:y=120:fontsize=64:fontcolor=0x1C1C1C" \
  -c:v libx264 -pix_fmt yuv420p -crf 23 -c:a aac -b:a 160k -ar 48000 -ac 2 \
  -metadata creation_time="$(date -u +%Y-%m-%dT%H:%M:%SZ)" -shortest "$OUT"
echo "wrote $OUT ($(printf %.1f "$DUR")s)"
