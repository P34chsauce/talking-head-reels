import React from "react";
import { Composition } from "remotion";
import { Reel, ReelProps } from "./Reel";

// Props come from lib/reels/captions.py (--props=props.json); these defaults only feed the studio.
const defaults: ReelProps = {
  video: "clip.mp4",
  durationInFrames: 90,
  fps: 30,
  phrases: [],
  emojis: [],
  style: {
    fontSize: 84,
    textColor: "#FFFFFF",
    highlightColor: "#FF6B4A",
    outlineColor: "#1C1C1C",
    outlineWidth: 10,
    captionCenterY: 0.68,
  },
};

export const Root: React.FC = () => (
  <Composition
    id="Reel"
    component={Reel}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={90}
    defaultProps={defaults}
    calculateMetadata={({ props }) => ({
      durationInFrames: props.durationInFrames,
      fps: props.fps,
    })}
  />
);
