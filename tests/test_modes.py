"""The four unified modes: policy, engine gating and P0 invariants."""

from __future__ import annotations

import json

from conftest import load_plugin, plugin_module

load_plugin()
config = plugin_module("config")
core = plugin_module("core")
engine = plugin_module("engine")
orchestrator = plugin_module("orchestrator")
redundancy = plugin_module("redundancy")
cache = plugin_module("cache")

_BUDGET = {"SUTRA_ENABLED": "1", "SUTRA_MAX_BYTES": "2000",
           "SUTRA_HEAD_BYTES": "1200", "SUTRA_TAIL_BYTES": "600"}


def _cfg(monkeypatch, **env):
    values = {**_BUDGET, **env}
    if "SUTRA_MODE" not in values:
        monkeypatch.delenv("SUTRA_MODE", raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return config.resolve()


def test_a_fresh_installation_defaults_to_balanced(monkeypatch):
    monkeypatch.delenv("SUTRA_MODE", raising=False)
    cfg = config.resolve()
    assert cfg.mode == "balanced"
    assert cfg.mode_source == "default"
    policy = orchestrator.policy_for(cfg)
    assert policy.passes == 2
    assert policy.language is True


def test_a_fresh_installation_optimizes_without_selecting_anything(monkeypatch):
    monkeypatch.delenv("SUTRA_MODE", raising=False)
    text = ("In order to make use of the queue, it is important to note that "
            "the worker is able to retry the request.\n") * 20
    cfg = config.resolve()
    outcome = core.process_text(text, cfg=cfg)
    assert outcome.changed is True
    assert len(outcome.output) < len(text)
    assert "in order to" not in outcome.output.lower()


def test_mode_alone_is_enough_configuration(monkeypatch):
    monkeypatch.setenv("SUTRA_MODE", "balanced")
    text = ("In order to make use of the queue, it is important to note that "
            "the worker is able to retry the request.\n") * 20
    cfg = config.resolve()
    outcome = core.process_text(text, cfg=cfg)
    assert outcome.changed is True
    assert len(outcome.output) < len(text)


def test_balanced_already_has_every_essential_plan(monkeypatch):
    policies = [orchestrator.policy_for(_cfg(monkeypatch, SUTRA_MODE=mode))
                for mode in ("balanced", "aggressive", "beast")]
    balanced = set(policies[0].plans)
    assert balanced == {"format", "caveman"}
    for policy in policies[1:]:
        assert balanced.issubset(set(policy.plans))


def test_balanced_turns_caveman_on_without_a_second_switch(monkeypatch):
    cfg = _cfg(monkeypatch, SUTRA_MODE="balanced")
    assert cfg.mode == "balanced"
    assert cfg.mode_source == "explicit"
    policy = orchestrator.policy_for(cfg)
    assert policy.language is True
    assert policy.passes == 2
    assert policy.combine is False


def test_aggressive_and_beast_grant_more_search(monkeypatch):
    aggressive = orchestrator.policy_for(
        _cfg(monkeypatch, SUTRA_MODE="aggressive"))
    beast = orchestrator.policy_for(_cfg(monkeypatch, SUTRA_MODE="beast"))
    assert aggressive.combine is True
    assert beast.triple is True
    assert beast.passes > aggressive.passes


def test_explicit_mode_wins_over_legacy_search(monkeypatch):
    cfg = _cfg(monkeypatch, SUTRA_MODE="beast", SUTRA_SEARCH="safe")
    assert cfg.mode == "beast"
    assert orchestrator.policy_for(cfg).combine is True


def test_an_explicit_legacy_off_is_not_upgraded_to_balanced(monkeypatch):
    cfg = _cfg(monkeypatch, SUTRA_SEARCH="off")
    assert cfg.mode == "off"
    assert cfg.mode_source == "legacy"
    assert orchestrator.policy_for(cfg).passes == 0


def test_legacy_safe_keeps_its_conservative_policy(monkeypatch):
    cfg = _cfg(monkeypatch, SUTRA_SEARCH="safe")
    policy = orchestrator.policy_for(cfg)
    assert cfg.mode == "balanced"
    assert cfg.mode_source == "legacy"
    assert policy.passes == 1
    assert policy.language is False
    assert policy.plans == ("format",)


def test_legacy_language_still_requires_its_own_switch(monkeypatch):
    without = orchestrator.policy_for(_cfg(monkeypatch, SUTRA_SEARCH="balanced"))
    with_language = orchestrator.policy_for(
        _cfg(monkeypatch, SUTRA_SEARCH="balanced", SUTRA_LANGUAGE="1"))
    assert without.language is False
    assert with_language.language is True


def test_explicit_off_disables_exact_duplicate_reuse(monkeypatch):
    legacy = _cfg(monkeypatch, SUTRA_REUSE="1")
    assert legacy.reuse_active is True
    monkeypatch.setenv("SUTRA_MODE", "off")
    cfg = config.resolve()
    assert cfg.reuse_active is False


def test_mode_file_is_read_and_env_wins(monkeypatch, tmp_path):
    path = tmp_path / "mode"
    monkeypatch.setenv("SUTRA_MODE_FILE", str(path))
    monkeypatch.delenv("SUTRA_MODE", raising=False)
    config.write_mode("aggressive")
    assert config.resolve().mode == "aggressive"
    monkeypatch.setenv("SUTRA_MODE", "balanced")
    assert config.resolve().mode == "balanced"


def test_unknown_mode_does_not_write_a_file(tmp_path):
    target = tmp_path / "mode"
    try:
        config.write_mode("turbo", str(target))
    except ValueError as exc:
        assert "turbo" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("write_mode accepted an unknown mode")
    assert not target.exists()


def test_balanced_applies_caveman_to_prose(monkeypatch):
    fillers = ("Please note that this line is able to use the cache in order "
               "to reduce latency.\n")
    text = fillers * 60
    cfg = _cfg(monkeypatch, SUTRA_MODE="balanced")
    out, stats = engine.compress(text, cfg=cfg)
    assert stats.changed
    assert "language" in stats.search_applied
    assert "in order to" not in out


def test_off_never_invokes_an_engine(monkeypatch):
    fillers = ("Please note that this line is able to use the cache in order "
               "to reduce latency.\n")
    text = fillers * 60
    cfg = _cfg(monkeypatch, SUTRA_MODE="off")
    out, stats = engine.compress(text, cfg=cfg)
    assert stats.search_applied == ""
    assert "in order to" in out  # no Caveman rewrite in off mode
    assert "language" not in stats.search_applied


def test_patch_is_byte_identical_in_beast_mode(monkeypatch):
    patch = ("diff --git a/a.py b/a.py\nindex 111..222 100644\n"
             "--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n")
    patch += "".join(f"-old line {i}   \n+new line {i}\n" for i in range(100))
    cfg = _cfg(monkeypatch, SUTRA_MODE="beast", SUTRA_MAX_BYTES="500")
    out, stats = engine.compress(patch, cfg=cfg)
    assert out == patch
    assert stats.search_applied == ""
    assert stats.rule == "diff-kept"


def test_structured_payload_is_never_searched():
    text = '{"note": "please   use the cache in order to reduce latency  "}'
    report = orchestrator.optimize_text(text, structured=True,
                                        cfg=config.Config(
                                            enabled=True, max_bytes=2000,
                                            head_bytes=1200, tail_bytes=600,
                                            json_items=0, stats_path="",
                                            stats_max_bytes=0, store_dir="",
                                            store_enabled=True,
                                            store_ttl_hours=1,
                                            store_max_bytes=1024,
                                            store_max_entry_bytes=1024,
                                            store_max_entries=1,
                                            recovery_max_bytes=512,
                                            recovery_max_matches=1,
                                            mode="beast",
                                            mode_source="explicit"))
    assert report.text == text
    assert report.chosen == ""
    assert "structured" in str(report.decision["reason"])


def test_search_plan_choice_is_explained(monkeypatch):
    prose = ("Please note that the worker is able to use the queue in order "
             "to reduce latency.\n") * 30
    report = orchestrator.optimize_text(
        prose, cfg=_cfg(monkeypatch, SUTRA_MODE="aggressive"))
    assert report.chosen == "caveman"
    assert report.saved_bytes > 0
    sace = report.decision["sace"]
    assert sace["chosen"] == "caveman"
    assert any(item["plan"] == "format" and not item["admitted"]
               for item in report.decision["trace"])


def test_core_keeps_the_original_when_the_store_is_disabled(monkeypatch):
    text = "".join(f"line {i:03d}   \n" for i in range(400))
    cfg = _cfg(monkeypatch, SUTRA_STORE="0")
    outcome = core.process_text(text, cfg=cfg)
    assert outcome.changed is False
    assert outcome.output == text


def test_core_keeps_the_original_when_it_cannot_store_it(monkeypatch):
    text = "".join(f"line {i:03d}   \n" for i in range(400))
    cfg = _cfg(monkeypatch, SUTRA_STORE_MAX_ENTRY_BYTES="1024")
    outcome = core.process_text(text, cfg=cfg)
    assert outcome.changed is False
    assert outcome.output == text


def test_ponytail_is_gated_by_mode(monkeypatch, tmp_path):
    ledger = redundancy.Ledger(tmp_path / "redundancy.json")
    fingerprint = redundancy.call_fingerprint("read_file", '{"path":"a.py"}')
    snapshot = redundancy.Snapshot(files={"a.py": "sha256:abc"})
    ledger.record(fingerprint, dependencies=snapshot,
                  result_fingerprint="result")
    balanced = orchestrator.decide_execution(
        fingerprint, ledger=ledger, dependencies=snapshot,
        cfg=_cfg(monkeypatch, SUTRA_MODE="balanced"))
    off = orchestrator.decide_execution(
        fingerprint, ledger=ledger, dependencies=snapshot,
        cfg=_cfg(monkeypatch, SUTRA_MODE="off"))
    assert balanced["action"] == "reuse"
    assert off["action"] == "execute"


def test_ponytail_never_skips_a_verification_command(monkeypatch, tmp_path):
    ledger = redundancy.Ledger(tmp_path / "redundancy.json")
    fingerprint = redundancy.call_fingerprint("bash", "pytest -q")
    snapshot = redundancy.Snapshot()
    ledger.record(fingerprint, dependencies=snapshot,
                  result_fingerprint="result")
    decision = orchestrator.decide_execution(
        fingerprint, ledger=ledger, dependencies=snapshot,
        command="pytest -q", cfg=_cfg(monkeypatch, SUTRA_MODE="beast"))
    assert decision["action"] == "execute"
    assert decision["classification"] == redundancy.MUST_EXECUTE


def test_context_selection_is_off_in_off_mode(monkeypatch):
    context = plugin_module("context")
    items = [context.Item("a", "x", token_cost=1, relevance=1.0)]
    assert orchestrator.optimize_context(
        items, 10, cfg=_cfg(monkeypatch, SUTRA_MODE="off")) is None
    selection = orchestrator.optimize_context(
        items, 10, cfg=_cfg(monkeypatch, SUTRA_MODE="balanced"))
    assert selection is not None
    assert selection.selected == ("a",)


def test_accounting_reports_a_positive_net_saving(monkeypatch):
    prose = "Please note that the worker is able to use the queue.\n" * 20
    report = orchestrator.optimize_text(
        prose, cfg=_cfg(monkeypatch, SUTRA_MODE="balanced"))
    accounted = orchestrator.accounting(report)
    assert accounted.billed_input_tokens <= accounted.original_input_tokens
    pricing = plugin_module("cost").Pricing(input_per_million=1.0,
                                            output_per_million=1.0)
    assert accounted.net_savings(pricing) >= 0
    assert accounted.baseline_estimated is True


def test_json_document_still_parses_after_beast_compression(monkeypatch):
    prose = "".join(
        f"It is important to note that worker {i} needs to check the queue.   \n"
        for i in range(200))
    payload = json.dumps({"stdout": prose, "exit_code": 0})
    cfg = _cfg(monkeypatch, SUTRA_MODE="beast")
    out, stats = engine.compress(payload, cfg=cfg)
    decoded = json.loads(out)
    assert decoded["exit_code"] == 0
    assert "stdout" in decoded
    assert stats.search_mode == "beast"


def test_mode_command_persists_and_clears(monkeypatch, tmp_path, capsys):
    cli = plugin_module("cli")
    path = tmp_path / "mode"
    monkeypatch.setenv("SUTRA_MODE_FILE", str(path))
    monkeypatch.delenv("SUTRA_MODE", raising=False)
    assert cli.main(["mode", "balanced"]) == 0
    assert "balanced" in path.read_text(encoding="utf-8")
    assert config.resolve().mode == "balanced"
    capsys.readouterr()
    assert cli.main(["mode", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "balanced"
    assert payload["source"] == "file"
    assert payload["policy"]["passes"] == 2
    assert any(engine["name"] == "ponytail" for engine in payload["engines"])
    assert cli.main(["mode", "--clear"]) == 0
    assert not path.exists()
    assert config.resolve().mode == "balanced"


def test_unknown_mode_file_falls_through_to_legacy(monkeypatch, tmp_path):
    path = tmp_path / "mode"
    path.write_text("turbo\n", encoding="utf-8")
    monkeypatch.setenv("SUTRA_MODE_FILE", str(path))
    monkeypatch.delenv("SUTRA_MODE", raising=False)
    monkeypatch.setenv("SUTRA_SEARCH", "aggressive")
    cfg = _cfg(monkeypatch)
    assert cfg.mode == "aggressive"
    assert cfg.mode_source == "legacy"
    assert orchestrator.policy_for(cfg).mode == "legacy:aggressive"


def test_cache_advice_is_mode_gated_and_uses_break_even(monkeypatch):
    economics = cache.Economics(write_cost=3.0, read_cost=1.0, miss_cost=5.0)
    off = _cfg(monkeypatch, SUTRA_MODE="off")
    assert orchestrator.cache_advice(economics, 10, cfg=off) is None
    active = _cfg(monkeypatch, SUTRA_MODE="balanced")
    advice = orchestrator.cache_advice(economics, 1, cfg=active)
    assert advice["mode"] == "balanced"
    assert advice["break_even_reuses"] == 0.75
    assert advice["worth_reusing"] is True
    assert orchestrator.cache_advice(economics, 0, cfg=active)["worth_reusing"] is False


def test_no_saving_plan_is_selected(monkeypatch):
    cfg = _cfg(monkeypatch, SUTRA_MODE="beast")
    report = orchestrator.optimize_text("plain identifiers and numbers 42\n",
                                        cfg=cfg)
    assert report.chosen == ""
    assert report.saved_bytes == 0
    assert report.decision["trace"]
    assert all(not item["admitted"] for item in report.decision["trace"])


def test_caveman_wins_alone_and_combination_adds_savings(monkeypatch):
    cfg = _cfg(monkeypatch, SUTRA_MODE="aggressive")
    prose = ("In order to make use of the system, it is important to note "
             "that a number of checks are performed on a regular basis.\n")
    alone = orchestrator.optimize_text(prose, cfg=cfg)
    assert alone.chosen == "caveman"
    assert alone.saved_bytes > 0

    mixed = ("    In order to make use of the system, it is important to note "
             "that a number of checks\u00a0are performed on a regular basis.   \r\n"
             "        Due to the fact that the process is slow, in the event "
             "that a failure occurs, it is able to retry.   \r\n")
    combined = orchestrator.optimize_text(mixed, cfg=cfg)
    assert combined.chosen == "format+caveman"
    savings = {item["plan"]: item["saved_bytes"]
               for item in combined.decision["trace"] if item["admitted"]}
    assert savings["caveman"] > 0
    assert savings["format"] > 0
    assert savings["format+caveman"] > savings["caveman"]
    assert savings["format+caveman"] > savings["format"]


def test_active_mode_delivers_a_lossless_rewrite_without_a_marker(monkeypatch):
    text = ("In order to make use of the queue, it is important to note that "
            "the worker is able to retry.\n")
    cfg = _cfg(monkeypatch, SUTRA_MODE="balanced", SUTRA_STORE="0")
    outcome = core.process_text(text, cfg=cfg)
    assert outcome.changed is True
    assert outcome.output != text
    assert "[sutra:" not in outcome.output
    assert "in order to" not in outcome.output.lower()


def _search_payload(rows: int = 400) -> str:
    return "\r\n".join(
        json.dumps({"id": index, "note": "  spaced  " * 4}) for index in range(rows)
    ) + "\r\n"


def _counting(real, log):
    name = getattr(real, "__name__", repr(real))

    def counted(value):
        log.append((name, value))
        return real(value)

    return counted


def test_the_plans_of_one_search_apply_each_strategy_once(monkeypatch):
    optimize = plugin_module("optimize")
    language = plugin_module("language")
    text = _search_payload()
    applied: list[tuple[int, str]] = []
    for name in ("_apply_crlf", "_apply_unicode_space", "_apply_trailing_space",
                 "_apply_blank_runs", "_apply_indent_trim"):
        monkeypatch.setattr(optimize, name,
                            _counting(getattr(optimize, name), applied))
    monkeypatch.setattr(language, "compress_text",
                        _counting(language.compress_text, applied))
    report = orchestrator.optimize_text(
        text, cfg=_cfg(monkeypatch, SUTRA_MODE="beast"))
    assert report.saved_bytes > 0, "the payload must reach the plans"
    duplicates = len(applied) - len(set(applied))
    assert duplicates == 0, f"{duplicates} (strategy, text) pairs ran twice"


def test_the_shared_memo_does_not_change_a_beast_search(monkeypatch):
    optimize = plugin_module("optimize")
    text = _search_payload()
    cached = orchestrator.optimize_text(
        text, cfg=_cfg(monkeypatch, SUTRA_MODE="beast"))
    real = optimize._apply
    monkeypatch.setattr(optimize, "_apply",
                        lambda strategy, value, memo=None: real(strategy, value, None))
    uncached = orchestrator.optimize_text(
        text, cfg=_cfg(monkeypatch, SUTRA_MODE="beast"))
    assert cached.text == uncached.text
    assert cached.saved_bytes == uncached.saved_bytes
    assert [(plan.plan, plan.saved_bytes, plan.applied) for plan in cached.plans] == [
        (plan.plan, plan.saved_bytes, plan.applied) for plan in uncached.plans]
