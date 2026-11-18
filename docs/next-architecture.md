# Where SUTRA can go next, and what each step would require

SUTRA currently runs as a post-tool-result hook: it sees one tool result, compresses
it, stores the original, and hands the compressed text back to the host. That is the
whole surface. Everything below asks a different question: what else is reachable, and
what would it cost to reach it?

This page is an investigation, not a roadmap promise. Nothing here is implemented on
this branch, and the measurements quoted are the ones in `docs/hermes-validation.md`
and `docs/benchmark.md`.

## What the measurements say about the current ceiling

In a real Hermes run against the isolated home, the first model request was
2,629 o200k tokens. The tool result that followed was 43,212 bytes (about 10k tokens)
and SUTRA returned 18,779 bytes (about 4.4k tokens). The compressed result is what
every later request in that conversation carries, so one tool result is paid for again
on every subsequent turn. The saving per compressed result therefore scales with the
number of remaining model calls, which is why an agent loop benefits more than a
single-shot call.

What the post-tool-result hook cannot do:

- It cannot see the conversation, so it cannot decide that an older tool result is no
  longer needed.
- It cannot see the request the model is about to receive, so it cannot shape the
  provider prompt prefix or report a cache hit.
- It cannot act before output is generated, so output tokens are out of reach.
- It cannot classify an operation the host performs without a tool result (repeated
  planning, repeated analysis inside one model call).

## The options

### 1. Pre-call request optimization

**Required access.** The assembled provider request (messages, tools, parameters),
before it is sent. In Hermes this is `llm_request` middleware
(`hermes_cli/middleware.py`, called from `agent/turn_api_request.py`); a plugin that
declares `llm_request` receives the request dict and may return `{"request": {...}}`
to replace it.

**Control needed.** Replace or drop message content, tools, or parameters for one
call. Must never mutate the stored conversation, only the outbound copy.

**Feasibility.** Confirmed available. `apply_llm_request_middleware` copies the
request, runs every registered middleware in order, and later middleware sees the
previous result. SUTRA does not currently declare this stage.

**Security.** The plugin sees the full prompt, including whatever the user pasted.
That is a larger trust surface than a tool result. It also sees tool schemas and
system instructions, so a bug can change agent behaviour rather than just shrink a log.

**Expected benefit.** Potentially the largest remaining lever: context selection over
the whole conversation, stale tool-result eviction, tool-schema trimming, and stable
prefixes for provider caching. The context selector (`context.py`) already exists and
could rank message segments instead of items.

**Complexity.** Medium in the plugin, high in validation: any dropped message can
change an answer, so an evaluator is mandatory.

**Compatibility.** Hermes-specific today. Other hosts would need the same hook or a
proxy.

**Validation.** Same corpus as `bench/agent/`, with an added check that the model can
still answer questions whose evidence was in a dropped message. Report quality deltas
per category, not an average.

### 2. Conversation-level context selection

**Required access.** The conversation plus a tokenizer for the target model. The
`llm_request` hook provides the message list; token counts need the provider's
tokenizer (o200k for OpenAI models; others differ).

**Control needed.** Drop or replace old messages, keep the system prompt and the last
user turn, preserve any message a tool call depends on.

**Feasibility.** The selection math is already implemented (`context.select`), but its
inputs (relevance, importance, recency) are caller-supplied today. Feeding it from a
real conversation needs a scoring policy that does not exist yet.

**Security.** Same as above, plus the risk of dropping the one message that carried a
constraint the user stated ten turns ago.

**Expected benefit.** Unknown until measured. Long agent sessions accumulate tool
results; a 50-call session re-sends a lot of stale output. But every dropped message is
also a chance to break the task, and providers already discount repeated prefixes via
prompt caching, so the billed gain can be smaller than the byte gain.

**Complexity.** High: selection policy, dependency tracking between tool calls and
results, and a quality evaluation.

**Compatibility.** Host-specific.

### 3. Provider prompt-cache visibility

**Required access.** The provider's usage metadata for each call, including cached
input tokens and cache-write tokens. In Hermes, `llm_execution` middleware wraps the
provider call, and every provider SDK returns the usage object; `providers.py` already
parses the four shapes.

**Control needed.** None to *report*. To *influence* the cache, the prompt prefix must
stay byte-identical across calls, which is a request-shaping decision (option 1).

**Feasibility.** Reporting is straightforward. Influencing is not something a hook can
guarantee: providers decide cache eligibility, and a single changed byte at the front
invalidates the prefix.

**Security.** None beyond handling the usage object; never log keys.

**Expected benefit.** Reporting turns an invisible discount into a measured number.
Shaping could increase hit rate, but a post-tool-result compressor that rewrites old
tool results is *hostile* to prefix caching, because it changes earlier messages
between calls. That trade-off has to be measured, not assumed.

**Complexity.** Low for reporting, medium for shaping plus regression tests on cache
hit rate.

**Validation.** Record `prompt_tokens`, `prompt_tokens_details.cached_tokens`, and
cache-write fields per call with the same corpus; compare hit rates across modes.

### 4. Pre-generation output controls

**Required access.** The request parameters before generation: `max_tokens`,
instructions, response format, stop sequences.

**Control needed.** Set or tighten them per task. This is request shaping again, so it
lives in option 1's hook.

**Feasibility.** Possible through `llm_request`, but the plugin would be guessing:
truncating output can cut a needed file body. Any cap must be conservative and
measured against task success.

**Expected benefit.** Output tokens are usually billed at a higher rate than input, so a
successful cap is valuable. `cost.py` already models separate input/output prices.

**Complexity.** Low to add a cap, high to prove it does not damage completions.

**Compatibility.** Any OpenAI-compatible provider accepts `max_tokens`, but not all
honour it identically.

### 5. Model and tool routing

**Required access.** The model choice and tool list per call. Hermes exposes
`llm.model_override` / `llm.provider_override` capabilities, which are for host-owned
calls, not for rewriting another agent's routing.

**Control needed.** Choose a cheaper model for simple calls, or a smaller tool set.

**Feasibility.** Technically possible in a host with routing hooks; not reachable from
the current post-tool hook. Routing also changes answer quality in ways that are hard
to attribute.

**Expected benefit.** Potentially large, but it is a different product: an agent
orchestrator rather than a compressor.

**Complexity.** High. **Risk.** High. Not recommended as a next step.

### 6. Safe semantic reuse

**Required access.** The ability to recognize that a new request is semantically the
same as a cached one, plus a guarantee that the cached answer is still valid.

**Feasibility.** Embeddings can cluster requests, but "same question" does not mean
"same answer": the repository changed, the user's constraint changed, the tool version
changed. The existing exact-reuse path (`reuse.py`) is safe precisely because it
requires a byte-identical input and a valid store entry.

**Recommendation.** Do not build semantic answer reuse. If semantic matching is used at
all, restrict it to *finding candidates* for exact validation, never to skipping work.

### 7. Host-level integration

**Required access.** A supported extension point, not a monkeypatch. Hermes has four
middleware stages (`tool_request`, `tool_execution`, `llm_request`, `llm_execution`),
plugin capabilities (`tools.override`, LLM overrides, `llm.side_question`), and a
plugin loader that reads `$HERMES_HOME/plugins`.

**Feasibility.** High, but it makes SUTRA host-specific. The current design deliberately
keeps a host-agnostic core (`core.process_text` / `core.process_result`) with thin
adapters, and that is what lets the same engine run under five agents today.

**Recommendation.** Add pre-call support as an *optional adapter*, keep the core
unchanged, and document which hosts have the hook.

### 8. Proxy architecture

**Required access.** Every request and response between the agent and the provider.

**Control needed.** Full request rewriting, streaming passthrough, usage capture.

**Feasibility.** A small OpenAI-compatible proxy is easy to write and hard to operate:
TLS, keys, retries, streaming, tool-call deltas, provider quirks, and a new place for
secrets to leak. It also inserts latency into every call.

**Expected benefit.** Maximum control: pre-call selection, prefix shaping, real usage
accounting, output caps.

**Complexity.** High. **Risk.** High: a proxy bug breaks the agent, not one log line.

**Recommendation.** Not before the `llm_request` experiment shows the remaining gain is
worth the operational cost.

## What I would do next, in order

1. **Measure the repeat-call effect.** The current harness records one tool call. Run a
   multi-turn scenario and count how many times a compressed tool result is re-sent;
   that number is the multiplier on every saving the current design produces.
2. **Build an experimental `llm_request` adapter** behind an env flag, using
   `context.select` on message segments with a conservative policy (drop only old tool
   results that no later message references). Measure with the same corpus and report
   quality deltas per category.
3. **Record provider usage** through `llm_execution` in both the control and the
   treatment, so cache hits and billed input are visible in the comparison.
4. Only if the numbers justify it, consider output caps and prefix shaping.

## What would falsify this plan

- If a multi-turn scenario shows the compressed result is re-sent many times, the
  current design is already capturing a large share of the addressable input tokens, and
  conversation selection has less headroom than it looks.
- If dropping old tool results changes task outcomes in the corpus, the pre-call
  approach needs a much better selection policy before it can ship.
- If provider cache hits already discount the repeated prefix heavily, billed savings
  from message eviction will be smaller than byte savings suggest.
