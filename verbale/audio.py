"""Capturing a meeting: two tracks, one ffmpeg, no mixing.

On PipeWire and PulseAudio every output device exposes a ".monitor" source
that carries exactly what is being played. Recording that gives you the far
end of a call without a virtual cable, a loopback module, or any cooperation
from the conferencing app. Recording the microphone at the same time gives you
your own side.

Both are captured by a single ffmpeg process with two inputs and two outputs,
rather than two processes, so the tracks start together and stay aligned.
Two processes drift apart by however long the second one took to start, and a
transcript whose speakers are half a second out of order reads like nonsense.
"""
import shutil
import signal
import subprocess
import time


class AudioError(RuntimeError):
    pass


def _pactl(*args):
    try:
        out = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        raise AudioError("pactl not found. Verbale needs PulseAudio or PipeWire.")
    except subprocess.TimeoutExpired:
        raise AudioError("pactl timed out. Is the audio server running?")
    if out.returncode != 0:
        raise AudioError(f"pactl {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def default_monitor():
    """The source that carries whatever your speakers are playing."""
    sink = _pactl("get-default-sink")
    if not sink:
        raise AudioError("no default audio output found")
    return f"{sink}.monitor"


def default_microphone():
    src = _pactl("get-default-source")
    if not src:
        raise AudioError("no default audio input found")
    return src


def list_sources():
    rows = []
    for line in _pactl("list", "short", "sources").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append({"index": parts[0], "name": parts[1],
                         "monitor": parts[1].endswith(".monitor")})
    return rows


def ffmpeg_path():
    path = shutil.which("ffmpeg")
    if not path:
        raise AudioError("ffmpeg not found. Install it and try again.")
    return path


class Recorder:
    """Records until you stop it. Use as a context manager.

    ffmpeg is asked to stop by writing "q" to its stdin rather than being
    killed, because a killed ffmpeg leaves the WAV header with a zero length
    field and every player then reports the file as empty.
    """

    def __init__(self, you_path, them_path, mic=None, monitor=None, rate=16000):
        self.you_path, self.them_path = you_path, them_path
        self.mic = mic or default_microphone()
        self.monitor = monitor or default_monitor()
        self.rate = rate
        self.proc = None
        self.started = None

    def command(self):
        return [
            ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "pulse", "-i", self.monitor,
            "-f", "pulse", "-i", self.mic,
            "-map", "0:a", "-ac", "1", "-ar", str(self.rate), "-c:a", "pcm_s16le",
            str(self.them_path),
            "-map", "1:a", "-ac", "1", "-ar", str(self.rate), "-c:a", "pcm_s16le",
            str(self.you_path),
        ]

    def start(self):
        self.proc = subprocess.Popen(
            self.command(), stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.started = time.time()
        time.sleep(0.4)
        if self.proc.poll() is not None:
            err = (self.proc.stderr.read() or "").strip()
            raise AudioError(f"ffmpeg exited immediately: {err or 'no output'}")
        return self

    def stop(self):
        if not self.proc or self.proc.poll() is not None:
            return self.duration()
        try:
            self.proc.stdin.write("q")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except (BrokenPipeError, ValueError, subprocess.TimeoutExpired):
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        return self.duration()

    def duration(self):
        return time.time() - self.started if self.started else 0.0

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


def wav_duration(path):
    """Seconds of audio in a PCM wav, read from the header. Cheap and stdlib."""
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0


def is_silent(path, threshold=1e-4):
    """True if a track carries no signal worth transcribing.

    A muted microphone still produces a perfectly valid file full of zeros,
    and handing that to Whisper wastes a minute and occasionally invents a
    sentence out of the noise floor.
    """
    import array
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2:
                return False          # only 16-bit PCM is ours to judge
            frames = w.readframes(min(w.getnframes(), w.getframerate() * 120))
            if not frames:
                return True
            # audioop would be one call, but it was removed in Python 3.13.
            samples = array.array("h")
            samples.frombytes(frames[: len(frames) // 2 * 2])
            peak = max(max(samples), -min(samples)) if samples else 0
            return peak / 32768.0 < threshold
    except Exception:
        return False


def read_pcm(path):
    """A wav as mono float32 in [-1, 1], plus its sample rate."""
    import array
    import wave
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise AudioError(f"{path} is not 16-bit PCM")
        rate, channels = w.getframerate(), w.getnchannels()
        samples = array.array("h")
        samples.frombytes(w.readframes(w.getnframes()))
    if channels > 1:
        samples = samples[::channels]
    try:
        import numpy as np
        return np.asarray(samples, dtype="float32") / 32768.0, rate
    except ImportError:
        return [s / 32768.0 for s in samples], rate


def speech_runs(pcm, rate, frame_ms=30, threshold=0.012, min_silence=0.7, pad=0.3):
    """Stretches of a track that actually contain speech.

    This exists because of how Verbale records. Each track is silent for every
    minute the other person is talking, and Whisper batches audio into windows
    of about thirty seconds. Hand it a raw track and a window will happily span
    a long silence, glue two of your sentences together, and stamp the pair
    with the time of the first. The transcript then claims you said something
    before a question you were in fact answering.

    So the silences are cut here instead, and each run of speech is
    transcribed on its own with its offset added back. It is also faster,
    because none of the silence is ever sent to the model.

    Returns a list of (start_seconds, end_seconds).
    """
    frame = max(1, int(rate * frame_ms / 1000))
    n = len(pcm)
    voiced = []
    for i in range(0, n, frame):
        chunk = pcm[i:i + frame]
        if not len(chunk):
            break
        # Mean absolute amplitude, not RMS: cheaper, and just as good at
        # separating "someone is talking" from "the room is quiet".
        try:
            level = float(sum(abs(x) for x in chunk)) / len(chunk)
        except TypeError:
            level = 0.0
        voiced.append(level >= threshold)

    runs, start = [], None
    gap_frames = max(1, int(min_silence * 1000 / frame_ms))
    quiet = 0
    for i, v in enumerate(voiced):
        if v:
            if start is None:
                start = i
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet >= gap_frames:
                runs.append((start, i - quiet + 1))
                start, quiet = None, 0
    if start is not None:
        runs.append((start, len(voiced)))

    out = []
    total = n / rate
    for a, b in runs:
        s = max(0.0, a * frame_ms / 1000 - pad)
        e = min(total, b * frame_ms / 1000 + pad)
        if e - s >= 0.25:
            out.append((s, e))
    return out
