"""Meetings on disk.

One directory per meeting, named so that `ls` sorts chronologically and a
human can tell what a directory is without opening it:

    ~/.local/share/verbale/2026-09-12-1030-product-standup/
        meeting.json     what it is, how long, which models touched it
        them.wav         what your speakers played
        you.wav          what your microphone heard
        transcript.json  segments with timings and speakers
        transcript.md    the same thing, readable
        notes.md         the summary

Nothing here needs Verbale to read it. That is the point: a notes tool you
cannot get your notes out of is a liability, and this one is `cat`.
"""
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STAMP = "%Y-%m-%d-%H%M"


def slugify(text, limit=40):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:limit].strip("-") or "meeting"


@dataclass
class Meeting:
    path: Path

    @property
    def id(self):
        return self.path.name

    @property
    def them_wav(self):
        return self.path / "them.wav"

    @property
    def you_wav(self):
        return self.path / "you.wav"

    @property
    def transcript_json(self):
        return self.path / "transcript.json"

    @property
    def transcript_md(self):
        return self.path / "transcript.md"

    @property
    def notes_md(self):
        return self.path / "notes.md"

    @property
    def meta_path(self):
        return self.path / "meeting.json"

    def meta(self):
        if self.meta_path.exists():
            try:
                return json.loads(self.meta_path.read_text())
            except json.JSONDecodeError:
                pass
        return {}

    def write_meta(self, **fields):
        meta = self.meta()
        meta.update(fields)
        self.meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
        return meta

    def segments(self):
        if not self.transcript_json.exists():
            return []
        try:
            return json.loads(self.transcript_json.read_text()).get("segments", [])
        except json.JSONDecodeError:
            return []

    def summary(self):
        return self.notes_md.read_text() if self.notes_md.exists() else ""

    def status(self):
        if self.notes_md.exists():
            return "summarised"
        if self.transcript_json.exists():
            return "transcribed"
        if self.them_wav.exists() or self.you_wav.exists():
            return "recorded"
        return "empty"

    def brief(self):
        meta = self.meta()
        return {
            "id": self.id,
            "name": meta.get("name") or self.id,
            "started": meta.get("started", ""),
            "duration": meta.get("duration", 0),
            "status": self.status(),
            "words": meta.get("words", 0),
        }


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, name=None, when=None):
        when = when or datetime.now()
        base = f"{when.strftime(STAMP)}-{slugify(name)}"
        path = self.root / base
        # Two meetings starting in the same minute is unlikely but not
        # impossible, and silently writing into an existing meeting would
        # destroy it.
        n = 2
        while path.exists():
            path = self.root / f"{base}-{n}"
            n += 1
        path.mkdir(parents=True)
        m = Meeting(path)
        m.write_meta(name=name or "Untitled meeting", started=when.isoformat(timespec="seconds"))
        return m

    def all(self):
        if not self.root.exists():
            return []
        dirs = [d for d in self.root.iterdir() if d.is_dir() and (d / "meeting.json").exists()]
        return [Meeting(d) for d in sorted(dirs, reverse=True)]

    def get(self, meeting_id):
        exact = self.root / meeting_id
        if exact.is_dir():
            return Meeting(exact)
        # Being able to type the first few characters of an id matters when
        # the id starts with a date you have to read off the screen.
        hits = [m for m in self.all() if m.id.startswith(meeting_id)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise KeyError(f"'{meeting_id}' matches {len(hits)} meetings")
        hits = [m for m in self.all() if meeting_id.lower() in m.brief()["name"].lower()]
        if len(hits) == 1:
            return hits[0]
        raise KeyError(f"no meeting matching '{meeting_id}'")

    def latest(self):
        allm = self.all()
        if not allm:
            raise KeyError("no meetings recorded yet")
        return allm[0]
