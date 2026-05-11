import time

from app.core.pipeline_timing import PipelineStopwatch


def test_pipeline_stopwatch_splits_monotonic_segments() -> None:
    sw = PipelineStopwatch()
    time.sleep(0.01)
    sw.split("a")
    time.sleep(0.01)
    sw.split("b")
    assert "a" in sw.rm_stage_ms
    assert "b" in sw.rm_stage_ms
    assert sw.rm_stage_ms["a"] >= 8.0
    assert sw.rm_stage_ms["b"] >= 8.0
