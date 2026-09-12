"""Speech to text, locally, with faster-whisper.

faster-whisper runs the same Whisper weights through CTranslate2, which is
several times quicker than the reference implementation on a CPU and is the
difference between a one hour meeting taking six minutes and taking half an
hour. Nothing is uploaded and no API key exists anywhere in this project.
"""
import sys

from .audio import is_silent, read_pcm, speech_runs, wav_duration

_MODEL_CACHE = {}

GPU_HINTS = ("libcublas", "libcudnn", "cuda", "cudnn", "no kernel image", "gpu")


def _whisper_model_class():
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit(
            "faster-whisper is not installed.\n"
            "  pipx install verbale        (installs it)\n"
            "  pip install faster-whisper  (if you are running from a checkout)"
        )
    return WhisperModel


def load_model(cfg, device=None, compute=None):
    """Loading a Whisper model takes seconds, so keep it for the process.

    The first call also downloads the weights, once, to the usual Hugging Face
    cache. That is the only time Verbale touches anything outside this
    machine, and you can do it deliberately with `verbale pull`.
    """
    device = device or cfg.whisper_device
    compute = compute or cfg.whisper_compute
    key = (cfg.whisper_model, device, compute)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = _whisper_model_class()(
            cfg.whisper_model, device=device, compute_type=compute)
    return _MODEL_CACHE[key]


def _looks_like_a_gpu_problem(exc):
    text = f"{type(exc).__name__} {exc}".lower()
    return any(h in text for h in GPU_HINTS)


def _run(model, path, cfg, total, on_progress):
    """Transcribe one track, one stretch of speech at a time.

    Each run of speech is sent separately and its timestamps are shifted back
    onto the real timeline, so a thirty second Whisper window can never span a
    silence and merge two turns that were minutes apart. The generator is
    drained here as well, so a GPU failure (which only happens on the first
    encode) surfaces inside the caller's try rather than halfway through
    writing a transcript.
    """
    pcm, rate = read_pcm(path)
    runs = speech_runs(pcm, rate) or [(0.0, total)]

    out, info = [], None
    for start, end in runs:
        clip = pcm[int(start * rate):int(end * rate)]
        segments, info = model.transcribe(
            clip,
            language=cfg.language or None,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            beam_size=5,
            condition_on_previous_text=False,   # stops one bad guess poisoning the rest
        )
        for seg in segments:
            text = seg.text.strip()
            if text:
                out.append({"start": start + float(seg.start),
                            "end": start + float(seg.end), "text": text})
        if on_progress and total:
            on_progress(min(1.0, end / total))
    return out, info


def transcribe_file(path, cfg, on_progress=None):
    """Transcribe one track. Returns (segments, info-dict).

    Voice activity detection matters more here than in a normal transcription
    job: each track is silent whenever the other person is talking, and
    without VAD Whisper will occasionally hallucinate speech into that silence
    (the classic "Thank you." at the end of an empty file).
    """
    if not path.exists() or wav_duration(path) < 0.2:
        return [], {"skipped": "missing or empty"}
    if is_silent(path):
        return [], {"skipped": "silent track"}

    total = wav_duration(path)
    device = cfg.whisper_device
    try:
        out, info = _run(load_model(cfg), path, cfg, total, on_progress)
    except Exception as e:
        # ctranslate2 is built with CUDA support, so device="auto" picks a GPU
        # whenever one is visible and then dies on a missing libcublas at the
        # first encode. A partial CUDA install should cost speed, not the
        # recording.
        if cfg.whisper_device == "cpu" or not _looks_like_a_gpu_problem(e):
            raise
        print(f"  gpu unusable ({type(e).__name__}), falling back to the cpu", file=sys.stderr)
        device = "cpu"
        out, info = _run(load_model(cfg, "cpu", "int8"), path, cfg, total, on_progress)

    if on_progress:
        on_progress(1.0)
    return out, {
        "language": getattr(info, "language", "") or cfg.language,
        "language_probability": round(float(getattr(info, "language_probability", 0) or 0), 3),
        "duration": round(total, 2),
        "device": device,
    }
