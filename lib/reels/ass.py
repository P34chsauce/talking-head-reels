"""ASS subtitle fallback: same phrases and highlight as the Remotion captions, no emojis
(libass can't draw colour emoji)."""
from __future__ import annotations


def _ts(t: float) -> str:
    cs = max(0, round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _bgr(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def build(phrases: list[dict], style: dict, width: int = 1080, height: int = 1920) -> str:
    y = round(style["captionCenterY"] * height)
    hi = _bgr(style["highlightColor"])
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{style["font"]},{style["fontSize"]},{_bgr(style["textColor"])},{hi},{_bgr(style["outlineColor"])},&H64000000&,-1,0,0,0,100,100,0,0,1,{style["outlineWidth"] // 2},2,5,110,110,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for p in phrases:
        ws = p["words"]
        for i, w in enumerate(ws):
            start = p["start"] if i == 0 else w["start"]
            end = ws[i + 1]["start"] if i + 1 < len(ws) else p["end"]
            if end <= start:
                continue
            fade = r"\fad(130,0)" if i == 0 else ""
            text = " ".join(
                f"{{\\c{hi}}}{_esc(x['w'])}{{\\r}}" if j == i else _esc(x["w"])
                for j, x in enumerate(ws))
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{{\\pos(540,{y}){fade}}}{text}")
    return head + "\n".join(lines) + "\n"
