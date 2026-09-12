from datetime import datetime

from verbale.store import Store, slugify


def test_slugify_handles_accents_and_punctuation():
    assert slugify("Perché? Sprint / Review!") == "perche-sprint-review"
    assert slugify("") == "meeting"
    assert slugify("   ") == "meeting"


def test_meeting_id_sorts_chronologically(tmp_path):
    s = Store(tmp_path)
    a = s.create("first", datetime(2026, 1, 2, 9, 0))
    b = s.create("second", datetime(2026, 3, 4, 15, 30))
    assert [m.id for m in s.all()] == [b.id, a.id]
    assert a.id == "2026-01-02-0900-first"


def test_two_meetings_in_the_same_minute_do_not_collide(tmp_path):
    s = Store(tmp_path)
    when = datetime(2026, 1, 1, 12, 0)
    a, b = s.create("sync", when), s.create("sync", when)
    assert a.id != b.id and a.path.exists() and b.path.exists()


def test_lookup_by_prefix_and_by_name(tmp_path):
    s = Store(tmp_path)
    m = s.create("Quarterly Review", datetime(2026, 5, 6, 11, 0))
    assert s.get("2026-05-06").id == m.id
    assert s.get("quarterly").id == m.id


def test_exact_id_wins_over_an_ambiguous_prefix(tmp_path):
    s = Store(tmp_path)
    when = datetime(2026, 1, 1, 12, 0)
    a, b = s.create("sync", when), s.create("sync", when)
    assert s.get(a.id).id == a.id
    assert s.get(b.id).id == b.id


def test_unknown_lookup_raises(tmp_path):
    s = Store(tmp_path)
    try:
        s.get("nothing")
        assert False, "should have raised"
    except KeyError:
        pass


def test_status_tracks_what_exists_on_disk(tmp_path):
    s = Store(tmp_path)
    m = s.create("demo")
    assert m.status() == "empty"
    m.them_wav.write_bytes(b"x")
    assert m.status() == "recorded"
    m.transcript_json.write_text('{"segments": []}')
    assert m.status() == "transcribed"
    m.notes_md.write_text("# notes")
    assert m.status() == "summarised"


def test_meta_survives_a_corrupt_file(tmp_path):
    s = Store(tmp_path)
    m = s.create("demo")
    m.meta_path.write_text("{not json")
    assert m.meta() == {}
    m.write_meta(name="recovered")
    assert m.meta()["name"] == "recovered"
