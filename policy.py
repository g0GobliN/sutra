"""Optional cost-aware execution guidance for the agent, not for its output.

The compression engine acts on a tool result after it exists. This module is
the independent policy layer: a short, clearly marked block of guidance that
Hermes freezes into each new session prompt through its documented
``register_system_prompt_section`` plugin API. Nothing here rewrites a prompt
the host built itself, and nothing here writes a user's configuration.

Three modes:

``off``
    No guidance at all. The registered callable returns an empty string, which
    Hermes skips, so the prompt is byte-identical to a session without SUTRA.
``balanced``
    The recommended mode: reuse valid information, read narrowly, avoid
    redundant calls, keep the evidence the task requires.
``aggressive``
    Adds stronger preferences about planning, combined checks and retrieval
    paging. It never permits skipping required work.

The text is deliberately guidance, not instruction replacement: it says that
the host's system and developer instructions, the user's task and repository
rules take precedence, and it names the work that must never be skipped. An
optimization that produces a cheaper but incorrect or incomplete answer is a
failure, so the safeguards are part of every non-off mode.

Injection is bounded and reversible:

- The section id is stable (``sutra-cost-policy``), so a second registration
  cannot append a duplicate. Hermes rejects a duplicate id; this module catches
  that and leaves the existing section alone.
- The content is a callable, resolved per new session, so ``SUTRA_POLICY=off``
  takes effect in the next session without editing anything.
- If the installed Hermes build has no system-prompt-section API, registration
  returns False and the plugin's compression and recovery keep working.
"""

from __future__ import annotations

import logging
from typing import Any

from . import config

logger = logging.getLogger(__name__)

SECTION_ID = "sutra-cost-policy"
POSITION = "after_memory"
# Hermes caps a plugin section at 4,000 characters. Keeping the same bound here
# means a section this module accepts is one the host will accept.
MAX_SECTION_CHARS = 4_000

OFF = "off"
BALANCED = "balanced"
AGGRESSIVE = "aggressive"
MODES = (OFF, BALANCED, AGGRESSIVE)

_BALANCED_TEXT = """SUTRA cost policy (mode: balanced)
Managed by SUTRA. These are efficiency preferences, not a replacement for the
host's system or developer instructions, the user's task, or repository rules.
When they conflict, the original instructions win.

- Reuse valid information already retrieved in this session. Re-read a source
  only when it may have changed, when the earlier result was compressed or
  truncated, or when the task requires fresh evidence.
- Read the narrowest thing that answers the question: a targeted search, a
  specific line range, or one section of a file rather than a whole tree.
- Do not repeat a tool call that already succeeded unless its inputs, the
  workspace, or the task have changed.
- Prefer commands with scoped output (grep, pytest -q, head, tail, --stat)
  over commands that produce large unfiltered output.
- Keep tool interactions concise and do not restate output you already hold.
- When a [sutra: ...] marker says output was compressed, use sutra_restore for
  the exact omitted bytes instead of re-running the command.
- Do not repeat verification that already passed against unchanged code.

Always preserve correctness: run the tests, checks and security review the task
requires; read the errors you are fixing; and report incomplete or failing work
honestly. Saving tokens is never a reason to skip required tests, ignore a
security warning or an error, omit required evidence, treat stale output as
current, reduce necessary investigation, or conceal unfinished work."""

_AGGRESSIVE_EXTRA = """
- Plan the smallest investigation that can establish the answer, then run it
  once.
- Prefer one combined check over several overlapping checks when it produces
  the same evidence.
- Retrieve only the omitted range you need from a marker; page with start/end
  instead of fetching the whole original.
- Do not narrate intermediate steps or re-summarise prior tool output."""

AGGRESSIVE_TEXT = _BALANCED_TEXT.replace(
    "mode: balanced", "mode: aggressive", 1).replace(
    "\n\nAlways preserve correctness:",
    _AGGRESSIVE_EXTRA + "\n\nAlways preserve correctness:")

_TEXT = {BALANCED: _BALANCED_TEXT, AGGRESSIVE: AGGRESSIVE_TEXT}


def mode(cfg: config.Config | None = None) -> str:
    """The effective policy mode.

    ``SUTRA_ENABLED=0`` turns the whole plugin off, policy included. A value
    outside the three documented modes is treated as ``off`` rather than
    guessing: guidance is only injected when it was explicitly recognised.
    """
    cfg = cfg or config.resolve()
    if not cfg.enabled:
        return OFF
    value = str(cfg.policy_mode or "").strip().lower()
    return value if value in _TEXT else OFF


def section(cfg: config.Config | None = None) -> str:
    """The exact text frozen into a new session prompt; empty in off mode."""
    current = mode(cfg)
    return _TEXT.get(current, "")


def support(ctx: Any) -> bool:
    """True when the host exposes the section API this module targets."""
    return callable(getattr(ctx, "register_system_prompt_section", None))


def register(ctx: Any, cfg: config.Config | None = None) -> bool:
    """Register the bounded section once; never raise into the plugin loader.

    Registration happens even in off mode so the callable's answer can change
    in a later session without reloading the plugin. In off mode the callable
    returns an empty string, which Hermes skips, so no prompt bytes are added.
    """
    if not support(ctx):
        logger.info(
            "sutra: this Hermes build has no system prompt section API; "
            "cost policy is not injected")
        return False
    try:
        ctx.register_system_prompt_section(
            SECTION_ID, _content, position=POSITION, max_chars=MAX_SECTION_CHARS)
    except Exception as exc:
        # A duplicate id from a previous load, or a host that rejects sections,
        # must never break compression and recovery.
        logger.warning("sutra: could not register the cost policy section: %s", exc)
        return False
    logger.debug("sutra: registered the cost policy section %s", SECTION_ID)
    return True


def _content(_session_info: Any = None) -> str:
    """Resolve the section text for one new session. Never raises."""
    try:
        return section()
    except Exception:  # a broken section is skipped by the host; keep it quiet
        logger.debug("sutra: could not resolve the cost policy section", exc_info=True)
        return ""


def describe(cfg: config.Config | None = None) -> dict[str, Any]:
    """A small, inspectable summary for status output and diagnostics."""
    cfg = cfg or config.resolve()
    current = mode(cfg)
    return {
        "mode": current,
        "section": SECTION_ID,
        "chars": len(section(cfg)),
    }


__all__ = [
    "AGGRESSIVE",
    "AGGRESSIVE_TEXT",
    "BALANCED",
    "MAX_SECTION_CHARS",
    "MODES",
    "OFF",
    "POSITION",
    "SECTION_ID",
    "describe",
    "mode",
    "register",
    "section",
    "support",
]
