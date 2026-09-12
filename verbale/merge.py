"""Turning two separately recorded tracks into one conversation.

This is the part that replaces speaker diarization. Verbale records what your
microphone hears and what your speakers play as two separate files, so every
segment already knows who produced it: nothing has to be inferred from voice
characteristics, which is the step that normally costs a second model, a GPU
and a pile of errors on cross-talk.

The price is that "Them" is one label for the whole far end of the call. For
notes that is usually the distinction that matters, and it is a price paid
honestly rather than a wrong guess at four different voices.
"""
from dataclasses import dataclass


@dataclass
class Segment:
    start: float
    end: float
    speaker: str
    text: str

    def as_dict(self):
        return {"start": round(self.start, 2), "end": round(self.end, 2),
                "speaker": self.speaker, "text": self.text}


def merge_tracks(you_segments, them_segments, you_label="You", them_label="Them",
                 join_gap=1.2):
    """Interleave two labelled tracks into one transcript, in time order.

    Consecutive segments from the same speaker separated by less than
    `join_gap` seconds are joined, because Whisper splits on breath pauses and
    a transcript broken every four words is unreadable.
    """
    segs = [Segment(s["start"], s["end"], you_label, s["text"].strip())
            for s in you_segments if s["text"].strip()]
    segs += [Segment(s["start"], s["end"], them_label, s["text"].strip())
             for s in them_segments if s["text"].strip()]
    segs.sort(key=lambda s: (s.start, s.end))

    out = []
    for s in segs:
        if out and out[-1].speaker == s.speaker and s.start - out[-1].end <= join_gap:
            prev = out[-1]
            prev.end = max(prev.end, s.end)
            prev.text = f"{prev.text} {s.text}".strip()
        else:
            out.append(Segment(s.start, s.end, s.speaker, s.text))
    return out


def stamp(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def to_markdown(segments):
    return "\n\n".join(f"**{s.speaker}** `{stamp(s.start)}`  \n{s.text}" for s in segments)


def to_plain(segments):
    """Speaker-prefixed plain text, which is what the summariser reads."""
    return "\n".join(f"{s.speaker}: {s.text}" for s in segments)
