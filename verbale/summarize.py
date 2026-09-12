"""Turning a transcript into notes, using a model running on this machine.

Ollama on localhost, and nothing else. If Ollama is not running you still have
a full transcript, which is the part that cannot be reconstructed later.
"""
import json
import urllib.error
import urllib.request

PROMPT = """You are writing the minutes of a meeting from its transcript.

The transcript labels each line with a speaker. "{you}" is the person whose \
machine recorded this. "{them}" is everyone else on the call.

Write the notes in Markdown with exactly these sections, in this order, and \
omit any section that genuinely has no content rather than padding it:

## Summary
Three to five sentences. What was this meeting actually about and what came out of it.

## Decisions
Bullet points. Only things that were actually decided, not things that were discussed.

## Action items
Bullet points as `- [ ] Owner: what they agreed to do`. Use "{you}" or "{them}" \
as the owner if no name was said. Only include commitments someone actually made.

## Open questions
Bullet points. Things raised and left unresolved.

Rules: do not invent anything that is not in the transcript. Do not add \
sections. Do not write a preamble or a closing remark. If the transcript is \
too short or too garbled to summarise, say so in one line under ## Summary \
and stop.

Transcript:
---
{transcript}
---
"""

REDUCE_PROMPT = """These are notes from consecutive parts of one meeting. \
Merge them into a single set of minutes with the same sections \
(## Summary, ## Decisions, ## Action items, ## Open questions). Remove \
duplicates, keep every distinct decision and action item, and do not invent \
anything.

{parts}
"""

# Roughly the point where a mid-sized local model starts forgetting the
# beginning of what it was given. Splitting is cheaper than a bad summary.
CHUNK_CHARS = 12000


def chunk_transcript(text, limit=CHUNK_CHARS):
    """Split on line boundaries so a speaker turn is never cut in half."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], []
    size = 0
    for line in text.splitlines():
        if size + len(line) + 1 > limit and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


class OllamaError(RuntimeError):
    pass


def available(cfg):
    try:
        with urllib.request.urlopen(f"{cfg.ollama_host}/api/tags", timeout=3) as r:
            return json.loads(r.read()).get("models", [])
    except Exception:
        return None


def generate(cfg, prompt):
    body = json.dumps({
        "model": cfg.ollama_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2},
    }).encode()
    req = urllib.request.Request(
        f"{cfg.ollama_host}/api/generate", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.summary_timeout) as r:
            return json.loads(r.read()).get("response", "").strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 404:
            raise OllamaError(
                f"model '{cfg.ollama_model}' is not pulled. Run: ollama pull {cfg.ollama_model}")
        raise OllamaError(f"Ollama returned {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise OllamaError(
            f"cannot reach Ollama at {cfg.ollama_host} ({e.reason}). "
            "Start it with `ollama serve`, or skip notes with --no-notes.")


def strip_thinking(text):
    """Reasoning models emit <think> blocks. They are not minutes."""
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    return text.strip()


def summarize(transcript, cfg, on_progress=None):
    chunks = chunk_transcript(transcript)
    parts = []
    for i, chunk in enumerate(chunks):
        if on_progress:
            on_progress(i, len(chunks))
        parts.append(strip_thinking(generate(
            cfg, PROMPT.format(transcript=chunk, you=cfg.you_label, them=cfg.them_label))))
    if len(parts) == 1:
        return parts[0]
    joined = "\n\n".join(f"### Part {i + 1}\n{p}" for i, p in enumerate(parts))
    if on_progress:
        on_progress(len(chunks), len(chunks))
    return strip_thinking(generate(cfg, REDUCE_PROMPT.format(parts=joined)))
