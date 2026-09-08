import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("control_windows_fixture", ROOT / "tools/renderer/control_windows.py")
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)
READY = {"sensitivity": "1", "m_yaw": "0.022", "m_pitch": "0.022", "sensitivity_y_scale": "1"}


@pytest.mark.parametrize("value", [None, {}, {"sensitivity": "1.096426"}, {"sensitivity": "NaN"},
                                     {"sensitivity": "1", "m_yaw": "0.05", "m_pitch": "0.022"}])
def test_executor_refuses_unobserved_or_changed_ready_settings(value):
    profile = SimpleNamespace(ready_requirements=READY)
    with pytest.raises(ValueError):
        worker.verify_ready_configuration({"synthetic_input_ready_convars": value}, profile)


def test_executor_accepts_numeric_readback_without_rewriting_historical_early_read():
    profile = SimpleNamespace(ready_requirements=READY)
    worker.verify_ready_configuration({"synthetic_input_ready_convars": {
        **READY, "sensitivity": "1.000000"}}, profile)


@pytest.mark.parametrize("value", [None, True, 1, "0.8", "NaN"])
def test_executor_requires_effective_vertical_scale_as_native_string(value):
    actual = {**READY, "sensitivity_y_scale": value}
    with pytest.raises(ValueError):
        worker.verify_ready_configuration({"synthetic_input_ready_convars": actual},
                                          SimpleNamespace(ready_requirements=READY))


def test_dry_run_does_not_create_game_or_evidence_outputs(tmp_path, capsys):
    out, evidence = tmp_path / "capture", tmp_path / "evidence"
    assert worker.main(["--output", str(out), "--evidence-output", str(evidence)]) == 0
    assert not out.exists() and not evidence.exists()
    assert '"decision_count": 256' in capsys.readouterr().out
