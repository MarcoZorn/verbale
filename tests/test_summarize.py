from verbale.summarize import PROMPT, chunk_transcript, strip_thinking


def test_short_transcripts_are_not_split():
    assert chunk_transcript("You: hello") == ["You: hello"]


def test_long_transcripts_split_on_line_boundaries():
    text = "\n".join(f"You: turn {i} " + "word " * 30 for i in range(300))
    chunks = chunk_transcript(text, limit=5000)
    assert len(chunks) > 1
    assert all(len(c) <= 5000 for c in chunks)
    assert "".join(c.replace("\n", "") for c in chunks) == text.replace("\n", "")
    assert all(c.startswith("You:") for c in chunks)


def test_a_single_enormous_line_is_not_lost():
    chunks = chunk_transcript("You: " + "x" * 9000, limit=1000)
    assert len("".join(chunks)) == 9005


def test_thinking_blocks_are_removed():
    assert strip_thinking("<think>\nplanning\n</think>\n## Summary\nok") == "## Summary\nok"
    assert strip_thinking("## Summary\nok") == "## Summary\nok"


def test_prompt_names_the_speaker_labels():
    p = PROMPT.format(transcript="You: hi", you="Marco", them="Client")
    assert "Marco" in p and "Client" in p and "You: hi" in p
    assert "do not invent" in p.lower()
