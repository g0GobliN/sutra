"""The A/B protocol's scorers are frozen before the first live run — CI holds them.

docs/ab-benchmark.md is the contract; scripts/ab/run.py implements it. These
tests keep the two in sync without any model: the scorer self-check must pass
(positive and negative), and every text fixture must be sized for the real
pipeline — big enough that SUTRA rewrites it (over the 24,576 B threshold),
small enough to survive Hermes' own 50,000-byte terminal cap.
"""

import importlib.util
import pathlib
import random
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sutra_ab", ROOT / "scripts" / "ab" / "run.py")
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)


def test_every_scorer_accepts_its_own_answer_and_rejects_a_wrong_one():
    assert ab.selfcheck() == 0


def test_every_text_fixture_is_sized_for_the_real_pipeline():
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="sutra-ab-size-"))
    for index, (name, _, setup, _) in enumerate(ab.TASKS):
        evidence = setup(workspace / name, random.Random(1_000 + index))
        if "test_cmd" in evidence:  # the coding task is a repo, not a payload
            continue
        size = ab.fixture_bytes(workspace / name, name)
        assert ab.MIN_PAYLOAD_BYTES <= size <= ab.MAX_PAYLOAD_BYTES, (name, size)


def test_buried_clue_is_elided_and_boundary_token_survives(sutra):
    """The two placement-sensitive tasks must hold against the real compressor:
    task 6's clue must vanish from the compressed copy (recovery is required),
    task 7's token must stay visible (boundary evidence is kept)."""
    for name, index, expected_visible in (("buried", 5, False), ("boundary", 6, True)):
        task, prompt, setup, scorer = ab.TASKS[index]
        del task, prompt, scorer
        workspace = pathlib.Path(tempfile.mkdtemp(prefix="sutra-ab-place-"))
        evidence = setup(workspace, random.Random(2_000 + index))
        payload_file = next(p for p in workspace.rglob("*")
                            if p.is_file() and p.stat().st_size > 1_000)
        out, stats = sutra.compress(payload_file.read_text(encoding="utf-8"))
        assert stats["rule"] == "head-tail", (name, stats["rule"])
        assert (evidence["planted"] in out) is expected_visible, name
