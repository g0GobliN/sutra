# Providers, tokenizers and usage metadata

SUTRA sits in a tool-result path. It reads bytes on the way back to the model,
writes a smaller result plus a recovery marker, and never calls a model
provider. That has three consequences this page states plainly:

1. **No tokenizer is universal.** The project measures tokens with
   `o200k_base` (via `tiktoken`, the encoding family OpenAI documents) and says
   so. Claude, Gemini and DeepSeek use different vocabularies, so a token count
   from one model is an estimate for another and must never be presented as a
   provider-measured figure.
2. **No price table ships.** Prices are per account, per model and per
   contract. `cost.Pricing` is supplied by the caller; without it every cost
   field is `null` with a reason.
3. **No universal compatibility claim.** Each agent integration is documented
   with the exact hook it uses and what it can prove in
   [integrations.md](integrations.md). The model providers below are the ones
   whose APIs make usage metadata available to their *clients*; SUTRA can only
   report it when the host passes it through.

## Model providers

| Provider | Input / output usage | Cached input | Cache-write tokens | Tokenizer SUTRA has | What SUTRA can do |
|---|---|---|---|---|---|
| OpenAI | `prompt_tokens`, `completion_tokens` | `prompt_tokens_details.cached_tokens` | not billed separately | `o200k_base` via tiktoken | accept all four counts from the host; price with caller rates |
| Anthropic | `input_tokens`, `output_tokens` | `cache_read_input_tokens` | `cache_creation_input_tokens` | none (different vocabulary) | accept counts; token figures are estimates; never claim provider cache savings |
| Google Gemini | `promptTokenCount`, `candidatesTokenCount` | `cachedContentTokenCount` | n/a | none | accept counts; estimates only |
| DeepSeek | `prompt_tokens`, `completion_tokens` | `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` | n/a | none | accept counts; estimates only |
| OpenAI-compatible | provider-dependent | provider-dependent | provider-dependent | `o200k_base` as an approximation | accept whatever the caller supplies; say "compatible", not "identical" |

Rules encoded in `cost.Accounting`:

- `cached_input_tokens` is a subset of billed input, never an addition.
- Cache-write tokens (Anthropic) are a separate charge with their own field:
  `cost.Usage.cache_write_tokens` and `cost.Pricing.cache_write_per_million`.
  A usage that reports cache writes but a pricing table without a cache-write
  rate prices as `None` instead of silently billing the writes at the plain
  input rate.
- `provider_latency_ms` and `total_latency_ms` are separate from SUTRA's own
  `processing_ms` and `validation_ms`, so overhead is never hidden in latency.
- `estimated_baseline` records whether the counterfactual was measured or
  estimated; the CLI does not guess.
- A token reduction is labelled comparable only when the tokenizer is named.

## Agents

The agent integrations are separate from model providers and are documented in
one place: [integrations.md](integrations.md) has the authoritative matrix
(mechanism, automatic or explicit, install, verification, limits) for Hermes,
Claude Code, OpenCode, Codex, OpenClaw, MCP and the CLI. None of them sees the
model's usage metadata by itself; a host that wants cost accounting passes the
provider's numbers to `sutra cost`.

## Token counting

`bench/maximum.py` uses `tiktoken.get_encoding("o200k_base")` when tiktoken is
installed and falls back to a stated approximation when it is not; the fallback
is labelled estimated. Nothing in the runtime hook needs tiktoken: the engine
works in bytes, and the byte savings it reports are measured exactly.

A token number in this project is therefore one of:

- **measured** — encoded with a named tokenizer in a benchmark run;
- **estimated** — the named approximation, labelled as such;
- **unavailable** — no tokenizer was named, so no comparison is offered.
