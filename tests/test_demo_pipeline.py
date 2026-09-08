"""Exercise the actual Windows-spawn-compatible process pools without CS2."""
import os
from pathlib import Path
import time

import pytest

from cs2_data import demo_pipeline as pipeline


def worker_probe(release, value):
    import pyarrow as pa
    from cs2_data import demo_pipeline
    os.environ["CS2_PIPELINE_PROBE"] = value
    demo_pipeline._events.put(os.getpid())
    deadline = time.monotonic() + 30
    while not Path(release).exists():
        if time.monotonic() > deadline:
            raise RuntimeError("Probe release timed out")
        time.sleep(0.02)
    return os.getpid(), os.environ["CS2_PIPELINE_PROBE"], pa.cpu_count(), pa.io_thread_count()


def test_recorder_and_two_validators_really_use_independent_processes(tmp_path):
    release = tmp_path/"release"
    with pipeline.create_workers(2) as (recorder, validators, _, events):
        tasks = [recorder.submit(worker_probe, str(release), "record")]
        tasks += [validators.submit(worker_probe, str(release), str(i)) for i in range(2)]
        try:
            pids = {events.get(timeout=20) for _ in tasks}
        finally:
            release.touch()
        results = [future.result(timeout=20) for future in tasks]
    assert len(pids) == 3 and os.getpid() not in pids
    assert [r[1] for r in results] == ["record", "0", "1"]
    assert all(r[2:] == (2, 2) for r in results)
    assert "CS2_PIPELINE_PROBE" not in os.environ


@pytest.mark.parametrize("value", [0, 5, -1, True, 2.5, "2"])
def test_worker_count_is_bounded(value):
    with pytest.raises(ValueError, match="1 and 4"):
        pipeline.worker_count(value)
