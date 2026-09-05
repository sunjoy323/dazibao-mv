"""Tests for LEAD clamp and concat — no overlap drift."""

from dazibao_mv.timeline import (
    TimedLine,
    assign_layouts,
    build_concat_list,
    clamp_timeline,
    no_overlap,
)


def test_lead_clamp_no_overlap():
    raw = [
        {"text": "A", "start": 1.0, "end": 2.0},
        {"text": "B", "start": 2.05, "end": 3.0},  # gap tiny; lead would overlap
        {"text": "C", "start": 5.0, "end": 6.0},
    ]
    lines = clamp_timeline(raw, lead=0.12, audio_dur=10.0)
    assert no_overlap(lines)
    # first can lead
    assert abs(lines[0].t0 - (1.0 - 0.12)) < 1e-6
    # second cannot go before prev_t1
    assert lines[1].t0 >= lines[0].t1 - 1e-9
    assert lines[2].t0 == max(lines[1].t1, 5.0 - 0.12)


def test_abutting_no_drift():
    """Many abutting lines must not accumulate +LEAD drift."""
    raw = []
    t = 0.0
    for i in range(20):
        raw.append({"text": f"L{i}", "start": t, "end": t + 1.0})
        t += 1.0
    lines = clamp_timeline(raw, lead=0.12, audio_dur=30.0)
    assert no_overlap(lines)
    # last t1 should stay near 20, not 20 + 20*0.12
    assert lines[-1].t1 <= 20.0 + 0.01
    assert lines[0].t0 == 0.0  # max(0, 0-lead)


def test_assign_layouts_separate_counters():
    lines = [
        TimedLine(text="v1", start=0, end=1, chorus=False),
        TimedLine(text="c1", start=1, end=2, chorus=True),
        TimedLine(text="v2", start=2, end=3, chorus=False),
        TimedLine(text="c2", start=3, end=4, chorus=True),
    ]
    layouts = ["A", "B", "C"]
    assign_layouts(lines, layouts)
    assert lines[0].layout == "A"  # verse 0
    assert lines[1].layout == "A"  # chorus 0
    assert lines[2].layout == "B"  # verse 1
    assert lines[3].layout == "B"  # chorus 1


def test_build_concat_with_gaps():
    lines = clamp_timeline(
        [
            {"text": "A", "start": 3.0, "end": 4.0},
            {"text": "B", "start": 6.0, "end": 7.0},
        ],
        lead=0.12,
        audio_dur=10.0,
    )
    parts = build_concat_list(lines, title_dur=2.0, audio_dur=10.0, fps=24)
    kinds = [p.kind for p in parts]
    assert kinds[0] == "title"
    assert "gap" in kinds
    assert kinds.count("line") == 2
    # end gap to audio_dur
    assert parts[-1].kind == "gap"
    assert abs(parts[-1].t1 - 10.0) < 1e-6


def test_min_duration():
    raw = [{"text": "X", "start": 1.0, "end": 1.05}]
    lines = clamp_timeline(raw, lead=0.0, audio_dur=5.0, min_dur=0.25)
    assert lines[0].t1 - lines[0].t0 >= 0.25 - 1e-9
