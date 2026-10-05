"""Judge calibration tooling tests (roadmap 2.1, D2) — pure functions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "judge_calibration.py"
spec = importlib.util.spec_from_file_location("judge_calibration", SCRIPT)
jc = importlib.util.module_from_spec(spec)
sys.modules["judge_calibration"] = jc
spec.loader.exec_module(jc)

from harness_loop.evals import bundled_tasks_path, load_tasks


class TestSampleTasks:
    def test_sample_size_and_determinism(self):
        tasks = load_tasks(bundled_tasks_path())
        first = jc.sample_tasks(tasks, n=30, seed=42)
        second = jc.sample_tasks(tasks, n=30, seed=42)
        assert len(first) == 30
        assert [t.id for t in first] == [t.id for t in second]

    def test_stratification_covers_categories(self):
        tasks = load_tasks(bundled_tasks_path())
        sample = jc.sample_tasks(tasks, n=30)
        categories = {t.category for t in sample}
        all_categories = {t.category for t in tasks}
        # with 15 categories and 30 slots every category must appear
        assert categories == all_categories

    def test_sample_larger_than_pool_is_capped(self):
        tasks = load_tasks(bundled_tasks_path())
        assert len(jc.sample_tasks(tasks, n=10_000)) == len(tasks)


class TestComputeMetrics:
    def test_perfect_judge(self):
        m = jc.compute_metrics(
            [True, True, False, False], [True, True, False, False]
        )
        assert (m["tp"], m["fp"], m["tn"], m["fn"]) == (2, 0, 2, 0)
        assert m["precision"] == 1.0 and m["recall"] == 1.0 and m["f1"] == 1.0

    def test_always_approving_judge(self):
        # human: 2 pass / 2 fail; judge approves everything -> recall 1, precision 0.5
        m = jc.compute_metrics([True, True, False, False], [True, True, True, True])
        assert (m["tp"], m["fp"], m["tn"], m["fn"]) == (2, 2, 0, 0)
        assert m["precision"] == 0.5
        assert m["recall"] == 1.0

    def test_always_rejecting_judge(self):
        m = jc.compute_metrics([True, True, False, False], [False, False, False, False])
        assert (m["tp"], m["fp"], m["tn"], m["fn"]) == (0, 0, 2, 2)
        assert m["precision"] == 0.0 and m["recall"] == 0.0

    def test_mismatched_lengths_rejected(self):
        with pytest.raises(ValueError):
            jc.compute_metrics([True], [True, False])

    def test_empty_inputs(self):
        m = jc.compute_metrics([], [])
        assert m["n"] == 0 and m["accuracy"] == 0.0


class TestPromptHash:
    def test_hash_is_stable_and_short(self):
        first = jc.prompt_hash()
        assert first == jc.prompt_hash()
        assert len(first) == 12

    def test_hash_tied_to_actual_prompts(self):
        import hashlib

        from harness_loop.judge import JUDGE_REFERENCE_SYSTEM_PROMPT, JUDGE_SYSTEM_PROMPT

        payload = (JUDGE_REFERENCE_SYSTEM_PROMPT + "\x00" + JUDGE_SYSTEM_PROMPT).encode("utf-8")
        assert jc.prompt_hash() == hashlib.sha256(payload).hexdigest()[:12]
