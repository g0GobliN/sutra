# Agent cost policy

**Status: implemented, unit-tested and validated with `hermes plugins validate`
on Hermes 0.22.7. The behavioural effect on a live model has not been
measured.**

Tool-output compression acts after a command has run. The agent policy acts
before the next one: it gives the model a short, bounded set of efficiency
preferences so it does not re-read what it already has, repeat a successful
call, or produce broad output it will not use.

The policy is guidance, not a replacement for instructions. It never claims
authority over the user's task, the host's system or developer instructions, or
repository rules, and every non-off mode names the work that must never be
skipped.

## Modes

| `SUTRA_POLICY` | Behaviour |
|---|---|
| `off` (default) | No policy text; the prompt is byte-identical to a session without SUTRA |
| `balanced` (recommended when enabled) | Reuse valid information, read narrowly, avoid redundant calls, keep required evidence |
| `aggressive` | Adds planning the smallest investigation, combining overlapping checks, paging retrieval; never permits skipping work |

`off` is the default because the replay shows the section costs 317 tokens on
every turn and no live run has yet shown that the guidance changes behaviour.
Compression and recovery are automatic and proven; the policy is one setting
away, not assumed.

Aliases: `0`/`false`/`no`/`disabled` mean `off`; `1`/`true`/`on` mean
`balanced`. An unrecognised value logs one warning and uses `off`.

`SUTRA_ENABLED=0` disables compression, retention and the policy together.

## How it is injected

Hermes exposes a plugin API for bounded session context:

```python
ctx.register_system_prompt_section(
    "sutra-cost-policy",
    content,                 # str or callable(session_info) -> str
    position="after_memory",
    max_chars=4_000,
)
```

SUTRA registers one section with that stable id. Hermes renders it once for
each new session and freezes it into that session's prompt. The API's own
limits apply: one section is capped at 4,000 characters and all plugin sections
together at 8,000 characters per session.

The content is a callable, so SUTRA resolves `SUTRA_POLICY` again for each new
session. Setting `SUTRA_POLICY=off` makes the callable return an empty string;
Hermes skips empty sections, so the next session receives no policy bytes. No
file is edited and no process needs restarting.

What SUTRA does **not** do:

- It does not modify `config.yaml`, `HERMES_HOME`, or any host prompt template.
- It does not wrap or call the host's prompt builder.
- It does not inject on every LLM request; it uses the host's per-session
  section contract.
- It does not write duplicate sections: a second registration with the same id
  is rejected by Hermes and caught by SUTRA.

If the installed Hermes build has no section API, registration reports
`False` and compression plus `sutra_restore` keep working. `sutra doctor
--client hermes` still validates the plugin through the host's own
`plugins validate` command.

## Safeguards

The text explicitly says that saving tokens is never a reason to:

- skip required tests or verification;
- ignore a security warning or an error;
- treat stale output as current;
- omit evidence the task requires;
- reduce necessary investigation;
- conceal unfinished or failing work.

It also states the precedence rule in the first paragraph: host instructions,
the user's task and repository rules win.

## Cost of the policy

The policy is not free: the section is part of the system prompt and is charged
on every turn of the session. `eval/replay.py` measures those tokens exactly
from the section text. The model-free replay cannot measure whether the
guidance changes behaviour, so it reports the policy's prompt cost separately
and does not claim a net saving from it.

## Verification

```bash
.venv/bin/python -m pytest tests/test_policy.py -q
.venv/bin/python -m pytest tests/test_eval_harness.py -q
sutra status          # optimization=on policy=balanced reuse=on recovery=on
sutra config          # resolved policy_mode, reuse and reuse_min_bytes
```

The policy tests cover all three modes, aliases, invalid values, repeated
registration, a missing API, a rejecting host, dynamic off, plugin
registration and the absence of configuration writes.
