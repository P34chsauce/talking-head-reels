"""Forced word alignment with WhisperX's wav2vec2 model. Runs under the separate WhisperX venv
(models/.wx, see README), not the main one, so torch stays out of the pipeline's own environment.

  models/.wx/bin/python lib/align_wx.py <whisper.cpp.json> <audio16k.wav> <out.json>

Input: whisper.cpp -oj output (sentence segments, Metal-fast). Output: WhisperX aligned segments.
"""
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import whisperx  # noqa: E402

src, wav, out = sys.argv[1:4]
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = json.load(open(src))
segments = [
    {"text": s["text"].strip(), "start": s["offsets"]["from"] / 1000, "end": s["offsets"]["to"] / 1000}
    for s in data["transcription"] if s["text"].strip()
]
audio = whisperx.load_audio(wav)
model, meta = whisperx.load_align_model(language_code="en", device="cpu",
                                        model_dir=os.path.join(root, "models", "hf"))
result = whisperx.align(segments, model, meta, audio, "cpu", return_char_alignments=False)
json.dump(result, open(out, "w"))
print(f"aligned {sum(len(s.get('words', [])) for s in result['segments'])} words")
