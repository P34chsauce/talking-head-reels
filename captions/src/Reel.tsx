import React from "react";
import {
  AbsoluteFill,
  OffthreadVideo,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont } from "@remotion/fonts";

type Word = { w: string; start: number; end: number };
type Phrase = { start: number; end: number; words: Word[] };
type Emoji = { emoji: string; start: number; end: number };

export type ReelProps = {
  video: string; // file name inside the job's --public-dir
  durationInFrames: number;
  fps: number;
  phrases: Phrase[];
  emojis: Emoji[];
  style: {
    fontSize: number;
    textColor: string;
    highlightColor: string;
    outlineColor: string;
    outlineWidth: number;
    captionCenterY: number;
  };
};

loadFont({
  family: "Atkinson Hyperlegible",
  url: staticFile("AtkinsonHyperlegible-Bold.ttf"),
  weight: "700",
});

// Gentle motion for reflective storytelling: a short fade + lift, no bounce.
const PhraseView: React.FC<{ phrase: Phrase; t: number; style: ReelProps["style"]; fps: number }> = ({
  phrase,
  t,
  style,
  fps,
}) => {
  const since = (t - phrase.start) * fps;
  const opacity = interpolate(since, [0, 4], [0, 1], { extrapolateRight: "clamp" });
  const lift = interpolate(since, [0, 6], [14, 0], { extrapolateRight: "clamp" });
  return (
    <div
      style={{
        position: "absolute",
        left: 110,
        right: 110,
        top: `${style.captionCenterY * 100}%`,
        transform: `translateY(calc(-50% + ${lift}px))`,
        opacity,
        textAlign: "center",
        textWrap: "balance", // "short walk / around the", never "short walk around / the"
        fontFamily: "Atkinson Hyperlegible",
        fontWeight: 700,
        fontSize: style.fontSize,
        lineHeight: 1.15,
      }}
    >
      {phrase.words.map((w, i) => {
        const active = t >= w.start && t < (phrase.words[i + 1]?.start ?? phrase.end);
        return (
          <span
            key={i}
            style={{
              color: active ? style.highlightColor : style.textColor,
              WebkitTextStroke: `${style.outlineWidth}px ${style.outlineColor}`,
              paintOrder: "stroke fill",
              textShadow: "0 6px 18px rgba(0,0,0,0.35)",
              display: "inline-block",
              transform: `scale(${active ? 1.06 : 1})`,
              margin: "0 0.14em",
            }}
          >
            {w.w}
          </span>
        );
      })}
    </div>
  );
};

const EmojiView: React.FC<{ e: Emoji; t: number; frame: number; fps: number; y: number }> = ({
  e,
  t,
  frame,
  fps,
  y,
}) => {
  const startFrame = Math.round(e.start * fps);
  const pop = spring({ frame: frame - startFrame, fps, config: { damping: 18, stiffness: 120 } });
  const out = interpolate(t, [e.end - 0.25, e.end], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: `${(y - 0.13) * 100}%`,
        textAlign: "center",
        fontSize: 160,
        // a soft light halo keeps dark emojis (🗑️ on a navy jacket) readable on any background
        filter: "drop-shadow(0 0 10px rgba(255,255,255,0.9)) drop-shadow(0 4px 12px rgba(0,0,0,0.35))",
        transform: `scale(${0.6 + 0.4 * pop})`,
        opacity: pop * out,
      }}
    >
      {e.emoji}
    </div>
  );
};

export const Reel: React.FC<ReelProps> = ({ video, phrases, emojis, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const phrase = phrases.find((p) => t >= p.start && t < p.end);
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <OffthreadVideo src={staticFile(video)} muted />
      {emojis
        .filter((e) => t >= e.start && t < e.end)
        .map((e, i) => (
          <EmojiView key={i} e={e} t={t} frame={frame} fps={fps} y={style.captionCenterY} />
        ))}
      {phrase ? <PhraseView phrase={phrase} t={t} style={style} fps={fps} /> : null}
    </AbsoluteFill>
  );
};
