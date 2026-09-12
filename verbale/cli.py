"""Command line interface."""
import argparse
import json
import shutil
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .audio import AudioError, Recorder, list_sources, wav_duration
from .config import CONFIG_PATH, Config
from .merge import merge_tracks, stamp, to_markdown, to_plain
from .store import Store

DIM, BOLD, ORANGE, GREEN, RED, OFF = "\033[2m", "\033[1m", "\033[38;5;209m", "\033[32m", "\033[31m", "\033[0m"


def colour(on):
    global DIM, BOLD, ORANGE, GREEN, RED, OFF
    if not on:
        DIM = BOLD = ORANGE = GREEN = RED = OFF = ""


def say(msg=""):
    print(msg, file=sys.stderr)


def hhmm(seconds):
    return stamp(seconds)


# --- record ----------------------------------------------------------------

def cmd_record(args, cfg, store):
    name = args.name or f"Meeting {datetime.now().strftime('%H:%M')}"
    meeting = store.create(name)
    try:
        rec = Recorder(meeting.you_wav, meeting.them_wav,
                       mic=args.mic, monitor=args.monitor, rate=cfg.sample_rate)
        say(f"{DIM}output {OFF}{rec.monitor}")
        say(f"{DIM}mic    {OFF}{rec.mic}")
        rec.start()
    except AudioError as e:
        shutil.rmtree(meeting.path, ignore_errors=True)
        raise SystemExit(f"{RED}{e}{OFF}")

    say(f"\n{ORANGE}recording{OFF} {BOLD}{name}{OFF}   {DIM}ctrl-c to stop{OFF}\n")
    stop = {"now": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("now", True))
    try:
        while not stop["now"]:
            elapsed = rec.duration()
            print(f"\r  {ORANGE}●{OFF} {hhmm(elapsed)}  ", end="", file=sys.stderr, flush=True)
            time.sleep(0.5)
    finally:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        duration = rec.stop()
    print("\r" + " " * 30 + "\r", end="", file=sys.stderr)

    meeting.write_meta(duration=round(duration, 1), sample_rate=cfg.sample_rate,
                       mic=rec.mic, monitor=rec.monitor)
    say(f"{GREEN}saved{OFF} {meeting.path}  ({hhmm(duration)})")

    if duration < 1.0:
        say(f"{DIM}too short to transcribe{OFF}")
        return 0
    if args.no_process:
        say(f"{DIM}run `verbale process {meeting.id}` when you want the notes{OFF}")
        return 0
    return run_pipeline(meeting, cfg, notes=not args.no_notes)


# --- pipeline --------------------------------------------------------------

def transcribe_meeting(meeting, cfg):
    from .transcribe import transcribe_file

    tracks = {}
    for label, path in ((cfg.you_label, meeting.you_wav), (cfg.them_label, meeting.them_wav)):
        if not path.exists():
            tracks[label] = ([], {"skipped": "no file"})
            continue
        say(f"{DIM}transcribing {path.name}{OFF}")
        bar = Progress(f"  {label:<5}")
        segs, info = transcribe_file(path, cfg, on_progress=bar.update)
        bar.done()
        if info.get("skipped"):
            say(f"  {DIM}{label}: {info['skipped']}{OFF}")
        tracks[label] = (segs, info)

    you, them = tracks[cfg.you_label][0], tracks[cfg.them_label][0]
    merged = merge_tracks(you, them, cfg.you_label, cfg.them_label)
    if not merged:
        raise SystemExit(f"{RED}nothing audible in either track{OFF}")

    words = sum(len(s.text.split()) for s in merged)
    meeting.transcript_json.write_text(json.dumps({
        "segments": [s.as_dict() for s in merged],
        "tracks": {k: v[1] for k, v in tracks.items()},
    }, indent=2, ensure_ascii=False) + "\n")
    meeting.transcript_md.write_text(
        f"# {meeting.meta().get('name', meeting.id)}\n\n{to_markdown(merged)}\n")
    meeting.write_meta(words=words, whisper_model=cfg.whisper_model,
                       transcribed=datetime.now().isoformat(timespec="seconds"))
    say(f"{GREEN}transcript{OFF} {words} words, {len(merged)} turns")
    return merged


def summarize_meeting(meeting, cfg, segments=None):
    from .summarize import OllamaError, summarize

    if segments:
        text = to_plain(segments)
    else:
        text = "\n".join(f"{s['speaker']}: {s['text']}" for s in meeting.segments())
    if not text.strip():
        raise SystemExit(f"{RED}no transcript to summarise. Run `verbale transcribe` first.{OFF}")

    def progress(i, total):
        if total > 1:
            say(f"{DIM}  summarising part {min(i + 1, total)}/{total}{OFF}")

    say(f"{DIM}summarising with {cfg.ollama_model}{OFF}")
    try:
        notes = summarize(text, cfg, on_progress=progress)
    except OllamaError as e:
        say(f"{RED}{e}{OFF}")
        say(f"{DIM}the transcript is saved either way: {meeting.transcript_md}{OFF}")
        return 1
    meeting.notes_md.write_text(f"# {meeting.meta().get('name', meeting.id)}\n\n{notes}\n")
    meeting.write_meta(ollama_model=cfg.ollama_model,
                       summarised=datetime.now().isoformat(timespec="seconds"))
    say(f"{GREEN}notes{OFF} {meeting.notes_md}")
    return 0


def run_pipeline(meeting, cfg, notes=True):
    segments = transcribe_meeting(meeting, cfg)
    if not notes:
        return 0
    return summarize_meeting(meeting, cfg, segments)


class Progress:
    """A one-line progress bar that stays quiet when nothing is a terminal."""

    def __init__(self, label, width=28):
        self.label, self.width = label, width
        self.on = sys.stderr.isatty()

    def update(self, fraction):
        if not self.on:
            return
        filled = int(self.width * max(0.0, min(1.0, fraction)))
        bar = "━" * filled + DIM + "━" * (self.width - filled) + OFF
        print(f"\r{self.label} {ORANGE}{bar}{OFF} {fraction * 100:3.0f}%",
              end="", file=sys.stderr, flush=True)

    def done(self):
        if self.on:
            print("\r" + " " * (self.width + len(self.label) + 12) + "\r",
                  end="", file=sys.stderr, flush=True)


# --- other commands --------------------------------------------------------

def cmd_list(args, cfg, store):
    meetings = store.all()
    if not meetings:
        say(f"{DIM}no meetings yet. Try: verbale record \"Standup\"{OFF}")
        return 0
    if args.json:
        print(json.dumps([m.brief() for m in meetings], indent=2))
        return 0
    mark = {"summarised": f"{GREEN}●{OFF}", "transcribed": f"{ORANGE}●{OFF}",
            "recorded": f"{DIM}●{OFF}", "empty": f"{RED}●{OFF}"}
    for m in meetings:
        b = m.brief()
        print(f"{mark[b['status']]} {BOLD}{b['id']}{OFF}  {b['name']}"
              f"  {DIM}{hhmm(b['duration'])}"
              f"{'  ' + str(b['words']) + 'w' if b['words'] else ''}{OFF}")
    return 0


def cmd_show(args, cfg, store):
    meeting = store.latest() if not args.id else store.get(args.id)
    if args.transcript:
        path = meeting.transcript_md
    elif args.json:
        print(json.dumps({"meta": meeting.meta(), "segments": meeting.segments()}, indent=2))
        return 0
    else:
        path = meeting.notes_md if meeting.notes_md.exists() else meeting.transcript_md
    if not path.exists():
        raise SystemExit(f"{RED}{meeting.id} has no {path.name} yet{OFF}")
    print(path.read_text())
    return 0


def cmd_process(args, cfg, store):
    meeting = store.latest() if not args.id else store.get(args.id)
    return run_pipeline(meeting, cfg, notes=not args.no_notes)


def cmd_transcribe(args, cfg, store):
    meeting = store.latest() if not args.id else store.get(args.id)
    transcribe_meeting(meeting, cfg)
    return 0


def cmd_notes(args, cfg, store):
    meeting = store.latest() if not args.id else store.get(args.id)
    return summarize_meeting(meeting, cfg)


def cmd_import(args, cfg, store):
    """Transcribe an audio file you already have.

    One file means one speaker label: there are no two tracks to tell apart.
    """
    import subprocess
    src = Path(args.file).expanduser()
    if not src.exists():
        raise SystemExit(f"{RED}no such file: {src}{OFF}")
    meeting = store.create(args.name or src.stem)
    say(f"{DIM}converting {src.name}{OFF}")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-ac", "1", "-ar", str(cfg.sample_rate), "-c:a", "pcm_s16le", str(meeting.them_wav)],
        check=True)
    meeting.write_meta(duration=round(wav_duration(meeting.them_wav), 1), source=str(src))
    return run_pipeline(meeting, cfg, notes=not args.no_notes)


def cmd_rm(args, cfg, store):
    meeting = store.get(args.id)
    if not args.yes:
        reply = input(f"delete {meeting.id} and its audio? [y/N] ").strip().lower()
        if reply != "y":
            return 1
    shutil.rmtree(meeting.path)
    say(f"{GREEN}deleted{OFF} {meeting.id}")
    return 0


def cmd_devices(args, cfg, store):
    for s in list_sources():
        tag = f"{ORANGE}output{OFF}" if s["monitor"] else f"{GREEN}input {OFF}"
        print(f"{tag}  {s['name']}")
    return 0


def cmd_web(args, cfg, store):
    from .server import serve
    serve(cfg, store, host=args.host or cfg.host, port=args.port or cfg.port, open_browser=not args.no_open)
    return 0


def cmd_doctor(args, cfg, store):
    from .summarize import available
    ok = True

    def check(label, good, detail=""):
        nonlocal ok
        ok = ok and good
        print(f"  {GREEN}ok  {OFF}" if good else f"  {RED}fail{OFF}", label,
              f"{DIM}{detail}{OFF}" if detail else "")

    print(f"\n{BOLD}Verbale {__version__}{OFF}\n")
    check("ffmpeg", bool(shutil.which("ffmpeg")), shutil.which("ffmpeg") or "not found")
    check("pactl", bool(shutil.which("pactl")), shutil.which("pactl") or "not found")
    try:
        from .audio import default_microphone, default_monitor
        check("audio output", True, default_monitor())
        check("microphone", True, default_microphone())
    except AudioError as e:
        check("audio devices", False, str(e))
    try:
        import faster_whisper  # noqa: F401
        check("faster-whisper", True, f"model '{cfg.whisper_model}'")
    except ImportError:
        check("faster-whisper", False, "pip install faster-whisper")
    models = available(cfg)
    if models is None:
        check("ollama", False, f"not reachable at {cfg.ollama_host}")
    else:
        names = [m.get("name", "") for m in models]
        check("ollama", True, f"{len(names)} models at {cfg.ollama_host}")
        check(f"model {cfg.ollama_model}", cfg.ollama_model in names,
              "" if cfg.ollama_model in names else f"ollama pull {cfg.ollama_model}")
    check("data directory", cfg.root.exists(), str(cfg.root))
    print()
    return 0 if ok else 1


def cmd_config(args, cfg, store):
    if args.path:
        print(CONFIG_PATH)
        return 0
    if args.write:
        print(f"{GREEN}wrote{OFF} {cfg.save()}")
        return 0
    from dataclasses import asdict
    print(json.dumps(asdict(cfg), indent=2))
    return 0


def cmd_pull(args, cfg, store):
    """Fetch the Whisper weights now, so the first meeting is not the download."""
    from .transcribe import load_model
    say(f"{DIM}fetching whisper '{cfg.whisper_model}'{OFF}")
    load_model(cfg)
    say(f"{GREEN}ready{OFF}")
    return 0


# --- entry point -----------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="verbale",
        description="Meeting notes that never leave your machine.")
    p.add_argument("--version", action="version", version=f"verbale {__version__}")
    p.add_argument("--no-colour", action="store_true", help="plain output")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("record", help="record a meeting, then transcribe and summarise it")
    r.add_argument("name", nargs="?", help="what to call it")
    r.add_argument("--mic", help="override the input device")
    r.add_argument("--monitor", help="override the output device to capture")
    r.add_argument("--no-notes", action="store_true", help="transcript only, no summary")
    r.add_argument("--no-process", action="store_true", help="record and stop there")
    r.set_defaults(fn=cmd_record)

    l = sub.add_parser("list", help="list recorded meetings")
    l.add_argument("--json", action="store_true")
    l.set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="print the notes (or the transcript)")
    s.add_argument("id", nargs="?", help="meeting id or name fragment, default the latest")
    s.add_argument("--transcript", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)

    pr = sub.add_parser("process", help="transcribe and summarise an existing recording")
    pr.add_argument("id", nargs="?")
    pr.add_argument("--no-notes", action="store_true")
    pr.set_defaults(fn=cmd_process)

    t = sub.add_parser("transcribe", help="(re)run transcription only")
    t.add_argument("id", nargs="?")
    t.set_defaults(fn=cmd_transcribe)

    n = sub.add_parser("notes", help="(re)run summarisation only")
    n.add_argument("id", nargs="?")
    n.set_defaults(fn=cmd_notes)

    i = sub.add_parser("import", help="transcribe an audio file you already have")
    i.add_argument("file")
    i.add_argument("--name")
    i.add_argument("--no-notes", action="store_true")
    i.set_defaults(fn=cmd_import)

    d = sub.add_parser("rm", help="delete a meeting and its audio")
    d.add_argument("id")
    d.add_argument("-y", "--yes", action="store_true")
    d.set_defaults(fn=cmd_rm)

    w = sub.add_parser("web", help="browse meetings in a local web page")
    w.add_argument("--port", type=int)
    w.add_argument("--host")
    w.add_argument("--no-open", action="store_true")
    w.set_defaults(fn=cmd_web)

    sub.add_parser("devices", help="list audio devices").set_defaults(fn=cmd_devices)
    sub.add_parser("doctor", help="check that everything is in place").set_defaults(fn=cmd_doctor)
    sub.add_parser("pull", help="download the Whisper model now").set_defaults(fn=cmd_pull)

    c = sub.add_parser("config", help="show or write the config file")
    c.add_argument("--write", action="store_true", help="write the defaults to disk")
    c.add_argument("--path", action="store_true", help="print the config path")
    c.set_defaults(fn=cmd_config)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    colour(not args.no_colour and sys.stderr.isatty())
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    cfg = Config.load()
    store = Store(cfg.root)
    try:
        return args.fn(args, cfg, store) or 0
    except KeyError as e:
        raise SystemExit(f"{RED}{e.args[0]}{OFF}")
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
