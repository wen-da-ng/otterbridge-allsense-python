import math

import allsense


def test_microphones_are_in_the_catalog(phone):
    assert [m.name for m in phone.microphones()] == ["mic"]


def test_audio_chunks_have_samples_and_times(phone):
    with phone.microphone("mic", chunk_seconds=0.1) as mic:
        chunks = list(mic)
    assert sum(len(c.samples) for c in chunks) == 4000 and chunks[0].rate == 16000
    assert chunks[0].t_ns == 5_000_000_000 and chunks[1].t_ns == 5_000_000_000 + 100_000_000  # 0.1 s later
    rms = math.sqrt(sum(s * s for s in chunks[0].samples) / len(chunks[0].samples))
    assert 5000 < rms < 6000  # an 8000-amplitude sine: rms = 8000 / sqrt(2)
