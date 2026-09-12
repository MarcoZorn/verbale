"""Configuration, and the one place that decides where anything is written.

Everything Verbale produces is a plain file under a single directory. No
database, no index, no cache that can disagree with what is on disk. You can
delete a meeting with rm, back it up with rsync, and read every artefact it
produced without this program installed.
"""
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _xdg(var, default):
    return Path(os.environ.get(var) or Path.home() / default)


DATA_DIR = Path(os.environ.get("VERBALE_HOME") or _xdg("XDG_DATA_HOME", ".local/share") / "verbale")
CONFIG_PATH = _xdg("XDG_CONFIG_HOME", ".config") / "verbale" / "config.json"


@dataclass
class Config:
    # Transcription. "small" is the honest default: "base" mangles names and
    # numbers often enough to make notes untrustworthy, and anything larger
    # is slower than real time on a laptop CPU.
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute: str = "default"
    language: str = ""          # "" lets Whisper detect it

    # Summarisation. Ollama on localhost, or nothing.
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.6:latest"
    summary_timeout: int = 600

    # Capture
    sample_rate: int = 16000    # what Whisper wants, so nothing resamples later
    you_label: str = "You"
    them_label: str = "Them"

    # Web UI
    host: str = "127.0.0.1"
    port: int = 7777

    data_dir: str = field(default_factory=lambda: str(DATA_DIR))

    @property
    def root(self):
        return Path(self.data_dir)

    @classmethod
    def load(cls):
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text())
            except json.JSONDecodeError as e:
                raise SystemExit(f"config at {CONFIG_PATH} is not valid JSON: {e}")
            known = {f for f in cls.__dataclass_fields__}
            unknown = set(raw) - known
            if unknown:
                raise SystemExit(
                    f"unknown option(s) in {CONFIG_PATH}: {', '.join(sorted(unknown))}"
                )
            cfg = cls(**{k: v for k, v in raw.items() if k in known})
        cfg.root.mkdir(parents=True, exist_ok=True)
        return cfg

    def save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2) + "\n")
        return CONFIG_PATH
