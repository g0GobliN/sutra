"""Deterministic property tests: random shapes, fixed seed, no new dependency.

The invariants here are the product's promises, checked over generated input
rather than hand-picked fixtures:

1. compression never raises;
2. it never grows a payload;
3. it never changes a payload's top-level JSON type;
4. it never touches a patch;
5. if it reports a change, the reported sizes match the bytes it returned.
"""

import json
import random

CHARS = "abcXYZ019 \t\r\n-+@:/'\"é日本語\x1b[31m\x1b[0m"


def _random_text(rng: random.Random, lines: int) -> str:
    out = []
    for _ in range(lines):
        kind = rng.random()
        if kind < 0.25:
            out.append(rng.choice(["same line", "tick ok", "---", "@@ -1 +1 @@"]))
        elif kind < 0.45:
            out.append("x" * rng.randint(1, 400))
        elif kind < 0.6:
            out.append("".join(rng.choice(CHARS) for _ in range(rng.randint(0, 40))))
        else:
            out.append(f"line {rng.randint(0, 10**6)} {rng.choice(['INFO', 'WARN', 'ERROR'])}")
    text = "\n".join(out)
    if rng.random() < 0.3:
        text += "\n"
    if rng.random() < 0.2:
        return text.replace("\n", "\r\n")
    return text


def _random_json(rng: random.Random) -> str:
    def value(depth):
        roll = rng.random()
        if depth > 3 or roll < 0.35:
            return rng.choice([
                "x" * rng.randint(0, 9_000),
                rng.randint(-10**12, 10**12),
                rng.random() < 0.5,
                None,
                "日本語" * rng.randint(0, 50),
            ])
        if roll < 0.65:
            return {f"k{i}": value(depth + 1) for i in range(rng.randint(0, 5))}
        return [value(depth + 1) for _ in range(rng.randint(0, 30))]

    document = value(0)
    return json.dumps(document, ensure_ascii=rng.random() < 0.5)


def test_generated_payloads_keep_the_promises(sutra):
    rng = random.Random(20_261_024)
    for _ in range(80):
        choice = rng.random()
        if choice < 0.5:
            text = _random_text(rng, rng.randint(0, 1_200))
        elif choice < 0.8:
            text = _random_json(rng)
        elif choice < 0.9:
            text = "diff --git a/x b/x\n@@ -1 +1 @@\n" + _random_text(rng, 400)
        else:
            text = "2c2\n< old\n---\n> new\n" + _random_text(rng, 400)
        max_bytes = rng.choice([300, 1_024, 2_048, 8_192, 24_576, 65_536])
        out, stats = sutra.compress(text, max_bytes=max_bytes)
        before = len(text.encode("utf-8"))
        after = len(out.encode("utf-8"))
        assert isinstance(out, str)
        assert after <= before, (max_bytes, before, after)
        assert stats["original_bytes"] == before
        assert stats["compressed_bytes"] == after
        assert stats["changed"] == (out != text)
        converted_in = sutra.engine._parse_json_document(text) is not None
        if converted_in:
            assert type(sutra.engine._parse_json_document(out)) is type(
                sutra.engine._parse_json_document(text))
        if sutra.engine._looks_like_patch(text):
            assert out == text


def test_generated_results_never_raise(sutra):
    rng = random.Random(7)
    shapes = [
        lambda: {"output": _random_text(rng, 500)},
        lambda: {"stdout": _random_text(rng, 500), "stderr": "", "exit_code": 3},
        lambda: {"result": {"nested": _random_text(rng, 500)}},
        lambda: {"output": _random_json(rng)},
        lambda: {"output": 42, "text": None},
        lambda: _random_text(rng, 50),
    ]
    for _ in range(40):
        value = rng.choice(shapes)()
        result = sutra.compress_result(value)
        assert result is not None
