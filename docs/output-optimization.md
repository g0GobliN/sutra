# Output optimization: before and after generation

Two different levers are easy to conflate: reducing what a model *reads*
(pre-generation) and reducing what a model *writes* (post-generation). SUTRA is
a tool-result path, so it can only do the second, and only for the text it is
given.

## What SUTRA controls

**Pre-generation input shaping** — the result a model is about to receive can be
shrunk before the next model call. That is exactly the tool-result path:

- oversized results are compressed by `engine.compress()` and replaced with a
  preview plus a recovery marker;
- `SUTRA_MAX_TOKENS` caps the preview size when the host allows configuration;
- a concise preview header states what was removed and how to restore it.

The savings here are *input* tokens: the model reads less on its next call.
They are measured in bytes and, when a tokenizer is named, in tokens
(`bench/maximum.py`).

**Post-generation output** — text a model already produced. SUTRA can store a
smaller copy for later reading (as it does for tool results), but it cannot
refund output tokens that were already billed. An output-side saving is only
real when the provider's own usage metadata shows fewer output tokens, which
means changing what the model was asked to produce, not post-processing its
answer.

## What this project does not do

- It does not rewrite prompts to ask for shorter answers. That changes model
  behaviour and belongs to the caller.
- It does not post-process a model's final answer to claim a token saving.
  Already-billed output tokens are not refundable.
- It does not shape the provider's prompt prefix to exploit provider-side
  caching. A post-tool hook cannot see the conversation, so it cannot place the
  stable prefix first; claiming that saving would be a fabrication.
- It does not report an output-token reduction unless a caller supplies output
  usage before and after a real change.

## How to measure the two separately

| Question | Metric | Where |
|---|---|---|
| Did the model read less? | input bytes and tokens per result | `bench/maximum.py`, [benchmark.md](benchmark.md) |
| Did the answer cost less? | provider-reported output tokens | `sutra cost --output N --output-price P` |
| What did SUTRA cost? | processing and validation time, `extra_cost` | [accounting.md](accounting.md) |
| Did quality hold? | task success, re-asks, restore rate | [ab-benchmark.md](ab-benchmark.md) (protocol; not yet run) |

A savings claim in this repository always names which of those rows it comes
from. The recovery marker is the reason an input saving does not silently
destroy information: the original is one `sutra restore` away, and
[recovery.md](recovery.md) states the bounds.
