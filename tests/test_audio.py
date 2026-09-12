import array
import math
import wave

from verbale.audio import Recorder, is_silent, wav_duration


def write_wav(path, amplitude, seconds=1, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(array.array(
            "h", [int(amplitude * math.sin(i / 8)) for i in range(rate * seconds)]).tobytes())


def test_silence_detection(tmp_path):
    quiet, loud = tmp_path / "q.wav", tmp_path / "l.wav"
    write_wav(quiet, 0)
    write_wav(loud, 12000)
    assert is_silent(quiet) is True
    assert is_silent(loud) is False


def test_duration_from_header(tmp_path):
    p = tmp_path / "a.wav"
    write_wav(p, 5000, seconds=3)
    assert abs(wav_duration(p) - 3) < 0.01


def test_missing_file_is_not_a_crash(tmp_path):
    assert wav_duration(tmp_path / "nope.wav") == 0.0
    assert is_silent(tmp_path / "nope.wav") is False


def test_command_writes_two_separate_tracks(tmp_path):
    r = Recorder(tmp_path / "you.wav", tmp_path / "them.wav",
                 mic="mic-device", monitor="monitor-device")
    cmd = " ".join(r.command())
    # one process, two inputs, two outputs: the tracks cannot drift apart
    assert cmd.count("-f pulse") == 2
    assert "monitor-device" in cmd and "mic-device" in cmd
    assert "you.wav" in cmd and "them.wav" in cmd
    assert "-ar 16000" in cmd


def test_speech_runs_splits_on_long_silence(tmp_path):
    """The reason this exists: each track is silent while the other person
    talks, and Whisper's 30 second windows will otherwise span that silence
    and glue two distant turns into one mis-stamped segment."""
    from verbale.audio import read_pcm, speech_runs
    rate = 16000
    p = tmp_path / "gappy.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        loud = array.array("h", [int(9000 * math.sin(i / 6)) for i in range(rate)])
        quiet = array.array("h", [0] * (rate * 3))
        w.writeframes((loud + quiet + loud + loud).tobytes())

    runs = speech_runs(*read_pcm(p))
    assert len(runs) == 2
    assert runs[0][0] < 0.2 and runs[0][1] < 2.0
    assert runs[1][0] > 3.0
    assert all(end > start for start, end in runs)


def test_speech_runs_keeps_short_pauses_together(tmp_path):
    from verbale.audio import read_pcm, speech_runs
    rate = 16000
    p = tmp_path / "breath.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        loud = array.array("h", [int(9000 * math.sin(i / 6)) for i in range(rate)])
        pause = array.array("h", [0] * int(rate * 0.3))   # a breath, not a turn
        w.writeframes((loud + pause + loud).tobytes())
    assert len(speech_runs(*read_pcm(p))) == 1


def test_silent_track_has_no_runs(tmp_path):
    from verbale.audio import read_pcm, speech_runs
    p = tmp_path / "silent.wav"
    write_wav(p, 0, seconds=5)
    assert speech_runs(*read_pcm(p)) == []
