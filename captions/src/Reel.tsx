import React from "react";
import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  Sequence,
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
// A cutaway, in seconds: footage over the talking head (full), or footage full frame with the
// speaker kept in a round face bubble in the corner (pip).
type Visual = {
  file: string;
  kind: "video" | "image";
  start: number;
  end: number;
  at: number;
  mode: "full" | "pip";
  aspect: number; // width / height
};

export type ReelProps = {
  video: string; // file name inside the job's --public-dir
  durationInFrames: number;
  fps: number;
  phrases: Phrase[];
  emojis: Emoji[];
  visuals?: Visual[];
  style: {
    fontSize: number;
    textColor: string;
    highlightColor: string;
    outlineColor: string;
    outlineWidth: number;
    captionCenterY: number;
    faceCenter?: [number, number]; // where the face sits in the talking-head frame, 0-1
    faceBubble?: number; // bubble diameter, px
    faceWidth?: number; // the face's width as a fraction of the frame (measured per clip)
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

const FADE = 4; // frames: a quick dissolve reads as an edit, a hard cut can look like a glitch

// The speaker's own video, cropped round on the face, kept in sync with the main clip.
const FaceBubble: React.FC<{ video: string; from: number; style: ReelProps["style"]; opacity: number }> = ({
  video,
  from,
  style,
  opacity,
}) => {
  const size = style.faceBubble ?? 300;
  const [fx, fy] = style.faceCenter ?? [0.5, 0.3];
  const w = (size * 0.6) / (style.faceWidth ?? 0.25); // zoom so the face fills ~60% of the bubble
  const h = (w * 1920) / 1080;
  return (
    <div
      style={{
        position: "absolute",
        right: 48,
        bottom: "7%",
        width: size,
        height: size,
        borderRadius: "50%",
        overflow: "hidden",
        border: "6px solid white",
        boxShadow: "0 12px 36px rgba(0,0,0,0.45)",
        opacity,
      }}
    >
      <OffthreadVideo
        src={staticFile(video)}
        trimBefore={from}
        muted
        style={{ position: "absolute", width: w, height: h, left: size / 2 - fx * w, top: size / 2 - fy * h }}
      />
    </div>
  );
};

// Inside its <Sequence>, so frame 0 is the cutaway's first frame.
const VisualView: React.FC<{ v: Visual; fps: number; video: string; style: ReelProps["style"] }> = ({
  v,
  fps,
  video,
  style,
}) => {
  const frame = useCurrentFrame();
  const total = Math.max(1, Math.round((v.end - v.start) * fps));
  const opacity = interpolate(frame, [0, FADE, total - FADE, total], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // Stills drift slowly so they don't read as a frozen video.
  const zoom = v.kind === "image" ? interpolate(frame, [0, total], [1, 1.08]) : 1;
  const media =
    v.kind === "video" ? (
      <OffthreadVideo
        src={staticFile(v.file)}
        trimBefore={Math.round(v.at * fps)}
        muted
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
    ) : (
      <Img
        src={staticFile(v.file)}
        style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${zoom})` }}
      />
    );
  return (
    <AbsoluteFill style={{ opacity, backgroundColor: "black" }}>
      {media}
      {v.mode === "pip" ? (
        <FaceBubble video={video} from={Math.round(v.start * fps)} style={style} opacity={opacity} />
      ) : null}
    </AbsoluteFill>
  );
};

export const Reel: React.FC<ReelProps> = ({ video, phrases, emojis, visuals = [], style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const phrase = phrases.find((p) => t >= p.start && t < p.end);
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <OffthreadVideo src={staticFile(video)} muted />
      {visuals.map((v, i) => (
        <Sequence
          key={i}
          from={Math.round(v.start * fps)}
          durationInFrames={Math.max(1, Math.round((v.end - v.start) * fps))}
          layout="none"
        >
          <VisualView v={v} fps={fps} video={video} style={style} />
        </Sequence>
      ))}
      {emojis
        .filter((e) => t >= e.start && t < e.end)
        .map((e, i) => (
          <EmojiView key={i} e={e} t={t} frame={frame} fps={fps} y={style.captionCenterY} />
        ))}
      {phrase ? <PhraseView phrase={phrase} t={t} style={style} fps={fps} /> : null}
    </AbsoluteFill>
  );
};
