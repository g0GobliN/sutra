"""Agent policy: bounded guidance, safe registration, no configuration writes."""

import pytest

from conftest import load_plugin, plugin_module

policy = plugin_module("policy")
config = plugin_module("config")
sutra = load_plugin()


class FakeCtx:
    """The documented Hermes registration surface, recorded for assertions."""

    def __init__(self, *, supports_sections=True, fail_sections=False):
        self.sections = []
        self.middleware = []
        self.tools = []
        self.supports_sections = supports_sections
        self.fail_sections = fail_sections

    def register_middleware(self, kind, callback):
        self.middleware.append((kind, callback))

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def __getattr__(self, name):
        if name == "register_system_prompt_section" and not self.supports_sections:
            raise AttributeError(name)
        if name == "register_system_prompt_section":
            return self._register_section
        raise AttributeError(name)

    def _register_section(self, section_id, content, *, position="after_memory",
                          max_chars=4_000):
        if self.fail_sections:
            raise RuntimeError("host rejected the section")
        if any(item[0] == section_id for item in self.sections):
            raise ValueError(f"system prompt section {section_id!r} is already registered")
        self.sections.append((section_id, content, position, max_chars))


def _normalised(text):
    return " ".join(text.split())


def test_off_adds_no_prompt_text(monkeypatch):
    monkeypatch.setenv("SUTRA_POLICY", "off")
    assert policy.mode() == "off"
    assert policy.section() == ""


@pytest.mark.parametrize("mode", ["balanced", "aggressive"])
def test_enabled_modes_produce_bounded_identified_guidance(monkeypatch, mode):
    monkeypatch.setenv("SUTRA_POLICY", mode)
    text = policy.section()
    assert text.startswith(f"SUTRA cost policy (mode: {mode})")
    assert "Managed by SUTRA" in text
    assert len(text) <= policy.MAX_SECTION_CHARS
    assert "<!-- hermes-plugin-sections:start -->" not in text
    flat = _normalised(text)
    for requirement in (
        "system or developer instructions",
        "user's task",
        "skip required tests",
        "security warning",
        "stale output",
        "omit required evidence",
        "reduce necessary investigation",
        "conceal unfinished work",
    ):
        assert requirement in flat


def test_aggressive_is_stricter_but_never_permissive(monkeypatch):
    monkeypatch.setenv("SUTRA_POLICY", "balanced")
    balanced = policy.section()
    monkeypatch.setenv("SUTRA_POLICY", "aggressive")
    aggressive = policy.section()
    assert len(aggressive) > len(balanced)
    assert "retrieve only the omitted range" in aggressive.lower()
    assert "Always preserve correctness" in aggressive
    assert "never a reason to skip required tests" in aggressive


def test_policy_aliases_and_invalid_values(monkeypatch):
    monkeypatch.setenv("SUTRA_POLICY", "0")
    assert policy.mode() == "off"
    monkeypatch.setenv("SUTRA_POLICY", "on")
    assert policy.mode() == "balanced"
    monkeypatch.setenv("SUTRA_POLICY", "turbo")
    assert policy.mode() == config.DEFAULT_POLICY_MODE


def test_disabling_the_plugin_disables_the_policy(monkeypatch):
    monkeypatch.setenv("SUTRA_POLICY", "aggressive")
    monkeypatch.setenv("SUTRA_ENABLED", "0")
    assert policy.mode() == "off"
    assert config.resolve().policy_mode == "off"


def test_register_uses_the_documented_section_api(monkeypatch):
    monkeypatch.setenv("SUTRA_POLICY", "balanced")
    ctx = FakeCtx()
    assert policy.register(ctx) is True
    assert len(ctx.sections) == 1
    section_id, content, position, max_chars = ctx.sections[0]
    assert section_id == policy.SECTION_ID
    assert position == "after_memory"
    assert max_chars == policy.MAX_SECTION_CHARS
    assert callable(content)
    assert content({}) == policy.section()


def test_repeated_registration_does_not_duplicate_or_raise(monkeypatch):
    monkeypatch.setenv("SUTRA_POLICY", "balanced")
    ctx = FakeCtx()
    assert policy.register(ctx) is True
    assert policy.register(ctx) is False  # the host rejected the duplicate id
    assert len(ctx.sections) == 1


def test_a_fresh_context_can_register_after_a_reload(monkeypatch):
    monkeypatch.setenv("SUTRA_POLICY", "balanced")
    first, second = FakeCtx(), FakeCtx()
    assert policy.register(first) is True
    assert policy.register(second) is True
    assert len(first.sections) == len(second.sections) == 1


def test_missing_capability_is_reported_not_faked():
    ctx = FakeCtx(supports_sections=False)
    assert policy.support(ctx) is False
    assert policy.register(ctx) is False
    assert ctx.sections == []


def test_a_rejecting_host_does_not_break_the_plugin(monkeypatch):
    monkeypatch.setenv("SUTRA_POLICY", "balanced")
    ctx = FakeCtx(fail_sections=True)
    assert policy.register(ctx) is False
    assert sutra.register(ctx) is None
    assert [kind for kind, _ in ctx.middleware] == ["tool_execution"]
    assert [tool["name"] for tool in ctx.tools] == ["sutra_restore"]


def test_policy_can_be_turned_off_without_re_registering(monkeypatch):
    monkeypatch.setenv("SUTRA_POLICY", "balanced")
    ctx = FakeCtx()
    assert policy.register(ctx) is True
    content = ctx.sections[0][1]
    assert content({}).startswith("SUTRA cost policy")
    monkeypatch.setenv("SUTRA_POLICY", "off")
    assert content({}) == ""  # the next new session gets the pre-0.5 prompt


def test_registration_writes_no_user_configuration(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("SUTRA_POLICY", "balanced")
    ctx = FakeCtx()
    assert policy.register(ctx) is True
    assert not home.exists()


def test_status_line_reports_the_resolved_optimization_state(monkeypatch):
    cli = plugin_module("cli")
    monkeypatch.setenv("SUTRA_POLICY", "aggressive")
    monkeypatch.setenv("SUTRA_REUSE", "0")
    line = cli._optimization_line()
    assert "optimization=on" in line
    assert "policy=aggressive" in line
    assert "reuse=off" in line
    assert "recovery=on" in line
    assert "max_bytes=24576" in line


def test_plugin_registers_middleware_tool_and_policy(monkeypatch):
    monkeypatch.setenv("SUTRA_POLICY", "balanced")
    ctx = FakeCtx()
    assert sutra.register(ctx) is None
    assert [kind for kind, _ in ctx.middleware] == ["tool_execution"]
    assert [tool["name"] for tool in ctx.tools] == ["sutra_restore"]
    assert [section[0] for section in ctx.sections] == [policy.SECTION_ID]
