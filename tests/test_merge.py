"""The two-track merge is what replaces diarization, so it is what gets tested."""
from verbale.merge import merge_tracks, stamp, to_markdown, to_plain


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


def test_interleaves_by_time():
    you = [seg(0, 1, "morning"), seg(6, 7, "agreed")]
    them = [seg(2, 5, "shall we start")]
    out = merge_tracks(you, them)
    assert [s.speaker for s in out] == ["You", "Them", "You"]
    assert [s.text for s in out] == ["morning", "shall we start", "agreed"]


def test_joins_the_same_speaker_across_breath_pauses():
    you = [seg(0, 1.0, "so the plan"), seg(1.4, 2.5, "is to ship friday")]
    out = merge_tracks(you, [])
    assert len(out) == 1
    assert out[0].text == "so the plan is to ship friday"
    assert out[0].end == 2.5


def test_does_not_join_across_a_real_gap():
    you = [seg(0, 1, "one"), seg(30, 31, "two")]
    assert len(merge_tracks(you, [])) == 2


def test_does_not_join_across_a_speaker_change():
    out = merge_tracks([seg(0, 1, "a"), seg(2, 3, "c")], [seg(1.1, 1.9, "b")])
    assert [s.text for s in out] == ["a", "b", "c"]


def test_drops_empty_and_whitespace_segments():
    out = merge_tracks([seg(0, 1, "   "), seg(2, 3, "real")], [seg(4, 5, "")])
    assert [s.text for s in out] == ["real"]


def test_custom_labels_are_used():
    out = merge_tracks([seg(0, 1, "hi")], [seg(2, 3, "hello")],
                       you_label="Marco", them_label="Client")
    assert [s.speaker for s in out] == ["Marco", "Client"]


def test_empty_input_is_empty_output():
    assert merge_tracks([], []) == []


def test_plain_text_is_what_the_model_reads():
    out = merge_tracks([seg(0, 1, "hi")], [seg(2, 3, "hello")])
    assert to_plain(out) == "You: hi\nThem: hello"


def test_markdown_carries_speaker_and_timestamp():
    md = to_markdown(merge_tracks([seg(65, 70, "hi")], []))
    assert "**You**" in md and "1:05" in md


def test_stamp_formats_hours_only_when_needed():
    assert stamp(0) == "0:00"
    assert stamp(65) == "1:05"
    assert stamp(3725) == "1:02:05"
    assert stamp(-5) == "0:00"
