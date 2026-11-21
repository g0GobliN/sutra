# The correctness contract

What SUTRA guarantees for every tool result it touches, what it explicitly does
not guarantee, and which tests enforce each claim. Every statement here describes
verified behavior — code plus the named tests — not intent. Where a guarantee
depends on the host (Hermes), the dependency is named.

Related: [recovery.md](recovery.md) (the retrieval protocol in full),
[semantic-retrieval.md](semantic-retrieval.md) (why ranked search is deferred),
[benchmark.md](benchmark.md) (what is measured and what is not),
[SECURITY.md](../SECURITY.md) (the security model), [../README.md](../README.md)
(operation and configuration).

## 1. Scope and pass-through

| Guarantee | Enforced by |
|---|---|
| A tool result of any type is returned as the same type. `str` in → `str` out; `dict` in → `dict` out with the same key set; anything else (`None`, numbers, lists, bytes, objects) is returned unchanged. | `test_unknown_result_shapes_pass_through`, `test_json_envelope_keeps_its_top_level_type_and_keys` |
| When Claude Code persists a large Bash/PowerShell result, the hook rewrites only the complete file named by `persistedOutputPath`; a symlink, a size disagreement, a path outside the session's `tool-results` tree, a non-Bash tool, or a decode failure leaves the result untouched. The 30,000-character preview is never stored as an "original". | `test_a_persisted_result_is_compressed_from_the_full_file`, `test_a_persisted_result_that_disagrees_with_its_size_is_left_alone`, `test_a_symlinked_persisted_result_is_never_followed`, `test_a_persisted_path_outside_the_transcript_tree_is_refused`, `test_persisted_metadata_from_a_non_bash_tool_is_not_read` |
| Only the top-level text fields of a dict result are candidates (`output`, `stdout`, `stderr`, `content`, `text`, `result`, `data`); nested structures and unknown keys are never rewritten. | probe P1/P7 (2026-10-24); `test_dict_result_compresses_text_fields_only` |
| Results at or below `SUTRA_MAX_BYTES` (24,576 B default) are returned untouched; `SUTRA_ENABLED=0` returns the input object itself. | `test_small_result_untouched`, `test_disabled_returns_result_untouched` |
| The pipeline is idempotent: feeding a rewritten result through again changes nothing. | probe P6 (2026-10-24) |

## 2. Size and budgets

| Guarantee | Enforced by |
|---|---|
| A rewrite — marker included — fits `max_bytes`. Head and tail are scaled proportionally when the configured budgets exceed it. | `test_max_bytes_is_a_real_cap`, `test_traceback_respects_max_bytes` |
| One JSON document shares one budget across its string fields; no document grows past the cap, and multi-field documents each keep their own share. | `test_json_document_stays_within_the_budget`, `test_json_fields_share_one_budget` |
| Budgets too small to be useful (head + tail under 64 B after the marker reserve) return the original with `rule: budget-too-small`. | `test_budget_too_small_returns_the_original` |
| Inputs above 64 MiB are never rewritten (`too-large-kept`). | probe P4 (2026-10-24); `store` refuses entries above `SUTRA_STORE_MAX_ENTRY_BYTES` |

## 3. Content that is never rewritten

| Guarantee | Enforced by |
|---|---|
| A payload that *is or contains* a patch — `diff --git`, `--- `/`+++ ` header pairs, `@@ ` hunks, or plain `diff` output (change command + `<`/`>` rows) — is returned byte-identical (`diff-kept`). One marker line anywhere protects the whole payload; this is deliberate conservatism, priced visibly in the benchmark (the patch row saves 0%). | `test_diff_is_never_rewritten`, `test_plain_unified_diff_without_git_prefix_is_never_rewritten`, `test_plain_diff_output_is_never_rewritten`, `test_a_patch_fragment_protects_the_whole_payload` |
| A traceback keeps its error end: at least 1 KiB of tail, at most 2 KiB of head, inside the same budget. | `test_traceback_keeps_the_error_end`, `test_traceback_respects_max_bytes` |
| A payload that is empty after ANSI stripping is not rewritten. | probe P2 (2026-10-24) |
| Text fidelity: CRLF and the final newline survive; cuts never split a UTF-8 character, and the marker's offsets name the cut boundaries in the stored original — the visible copy may drop ANSI escapes or collapsed lines after the offsets are fixed; repeat collapse removes whole lines and preserves terminators. | `test_crlf_and_final_newline_are_preserved`, `test_missing_final_newline_is_preserved`, `test_unicode_cut_does_not_produce_replacement_characters`, `test_a_head_cut_inside_a_character_is_moved_to_its_boundary`, `test_a_tail_cut_inside_a_character_is_moved_to_its_boundary`, `test_collapse_repeats_preserves_line_endings` |
| Text that cannot be encoded as UTF-8 — a lone surrogate such as the JSON escape `\ud800` — is returned unchanged (`invalid-utf8-kept`), with no replacement character or `?`, no marker and no id. Because it has no UTF-8 representation, both reported byte counts are 0. | `test_unencodable_text_is_never_replaced`, `test_compress_preserves_text_that_is_not_utf8_encodable` |
| Transport boundaries never silently repair bytes: the CLI rejects invalid UTF-8 on stdin or `--file` with exit 1 and an explicit error; the Claude Code hook returns `{}` for an invalid-UTF-8 envelope; the MCP server returns one `-32700` for that line and keeps serving. JSON responses are ASCII-escaped, so a non-UTF-8 stdout cannot fail them. | `test_invalid_utf8_on_stdin_is_rejected_not_replaced`, `test_invalid_utf8_file_is_rejected_not_replaced`, `test_malformed_and_unexpected_inputs_fail_open`, `test_invalid_utf8_is_one_parse_error_and_the_server_survives`, `test_non_ascii_output_survives_an_ascii_stdout` |

## 4. Markers — the machine-readable surface

Every content-eliding rewrite carries one single-line marker stating:
completeness (`partial`), the omitted byte count and reason, the JSON pointer
when the elision is inside a field, the retention id, the retention window, and
an exact `sutra_restore` call for the omitted range (byte offsets) or the
omitted lines (repeat collapse). A lossless rewrite — formatting or wording
with `omitted_bytes == 0` — changes the text without a marker because there is
no omitted content to recover.

| Guarantee | Enforced by |
|---|---|
| The marker is the only place omission is recorded, and it never claims bytes it did not keep. If the store is disabled, the entry is over a limit, the write fails, or the marker pass fails, the original is returned unchanged with no marker and no id; `stats.rule` records which recovery step refused. | `test_disabled_store_keeps_the_original`, `test_a_write_that_pushes_the_store_over_quota_keeps_the_original`, `test_a_markerless_rewrite_is_rejected_not_merely_unclaimed`, `test_every_marker_renders_exactly` |
| Every emitted retrieval hint parses as a Python call and returns evidence when executed against the real store. | `test_every_marker_hint_parses_and_runs` |
| Byte offsets address the stored original — computed before ANSI stripping and de-duplication, never against the rewritten copy. | `test_byte_offsets_address_the_stored_original_after_dedupe_and_ansi` |
| Repeat-collapse markers name the omitted line range in the retained original; where line numbers cannot be aligned (exotic breaks, tail fragments), the marker says so and quotes no call. | `test_repeat_collapse_hint_returns_the_omitted_lines`, `test_tail_dedupe_hint_uses_original_line_numbers`, `test_unaddressable_line_breaks_quote_no_hint` |
| A field pointer is emitted only when it can be quoted faithfully and resolved by recovery (≤256 UTF-8 bytes, no `"` `\` newline `]` `)`); such fields are left uncompressed on both passes. | `test_field_pointer_over_the_budget_is_not_truncated`, `test_field_with_unquotable_key_is_left_alone` |

## 5. JSON documents

| Guarantee | Enforced by |
|---|---|
| A JSON document in is a JSON document out, same top-level type, never byte-sliced; `json.loads` on the output succeeds or the original is returned (`json-kept`). | `test_json_document_is_never_byte_sliced`, `test_json_invariant_guard_returns_the_original`, fuzz invariant in `test_generated_payloads_keep_the_promises` |
| Only string values are rewritten; numbers, booleans, nulls, array shapes and key sets are untouched. | `test_json_document_with_only_small_strings_is_left_alone`, `test_json_array_stays_an_array` |
| Re-encoding normalizes whitespace/indentation and collapses duplicate keys to the last value. This is a **visible-copy change**; the original bytes stay in the store and markers address them. | documented here and in README "Known limitations"; `test_json_with_escaped_unicode_stays_valid` (round-trip) |
| Large arrays with no large string fields are returned untouched (`json-kept`) — the price of keeping their shape. A preview that elides or replaces elements is never emitted: it can pass the top-level-type check while changing element count, element types or key sets. The evaluation is recorded in [json-array-preview.md](json-array-preview.md). | `test_json_array_of_small_objects_is_returned_byte_identical`, benchmark row "8,000-item JSON array" |
| `SUTRA_JSON_ITEMS` is retired: parsed, warned about once, ignored. | `test_json_retired_array_option_is_ignored` |

## 6. Recovery (`sutra_restore`)

Full protocol: [recovery.md](recovery.md). The guarantees:

| Guarantee | Enforced by |
|---|---|
| Paged `mode=bytes` is byte-exact: concatenating successive pages reproduces the original slice, multi-byte text included; paging stops on UTF-8 boundaries. A requested range whose `start` or `end` splits a character is an explicit `error`, never a short page that claims the whole range. A literal U+FFFD is returned as content, never trimmed as a partial-decode artifact. | `test_bytes_mode_pages_out_the_exact_original_slice`, `test_byte_mode_preserves_literal_replacement_characters`, `test_byte_mode_preserves_a_replacement_character_at_a_page_start`, `test_byte_mode_rejects_ranges_that_split_a_character`, `test_byte_restore_rejects_a_range_that_splits_a_character`, `test_byte_restore_preserves_a_literal_replacement_character`, `test_byte_restore_preserves_a_literal_replacement_character_at_the_range_end` |
| Search is literal, bounded and has no user-supplied regex surface. `case_sensitive=false` escapes the query before matching it, so metacharacters stay literal and a clipped long line is centred on the original match, not on a length-changing lowercase copy. | `test_search_is_literal_and_case_sensitive_by_default`, `test_search_treats_regex_metacharacters_literally`, `test_case_insensitive_search_keeps_the_match_in_a_clipped_line`, `test_case_insensitive_restore_keeps_a_match_inside_a_clipped_line` |
| Every request returns an explicit status — `ok`, `not_found`, `expired`, `too_large`, `unavailable`, `error` — and never raises into the agent loop. | `test_restore_never_raises`, `test_missing_and_malformed_ids`, `test_invalid_mode_is_an_error` |
| Returned content is bounded: 8 KiB default, 64 KiB hard cap, ≤20 matches, ≤200-char literal query, no regex. | `test_lines_mode_is_bounded`, `test_search_respects_max_matches_and_reports_more`, `test_query_length_is_bounded` |
| `mode=full` reports `sha256_verified` from the store's own digest check over exactly the bytes returned. | `test_full_mode_verifies_the_hash`, `test_full_mode_uses_the_store_verifier`, `test_full_mode_reports_a_corrupted_entry` |
| Field-scoped requests resolve RFC 6901 pointers against the stored original and serve that field's bytes; unresolvable pointers error without content. | `test_field_scope_returns_the_exact_field_text`, `test_unresolvable_field_pointer_errors_without_content` |
| Every issued id resolves to the original it was issued for, or to an explicit failure (`not_found`/`expired`) — never to a different payload. | `test_end_to_end_journey`, `test_profiles_do_not_share_the_store` |
| Host dependency: the tool is visible to the model only when the `sutra` toolset is enabled for the platform. With Hermes' Tool Search active, plugin tools may additionally be **deferred** behind the `tool_search`/`tool_describe`/`tool_call` bridge; the tool still exists and dispatches, but a model may need to search for it first. Operators can keep it eager via Hermes' `tools.tool_search` defer configuration. | verified against Hermes v0.22.2+3030.g4d04303 (2026-10-24, see baseline mission log); not testable without the host |

## 7. Storage and security

Full model: [SECURITY.md](../SECURITY.md). The guarantees:

| Guarantee | Enforced by |
|---|---|
| Originals are written atomically (temp + rename), `0600`, `O_NOFOLLOW` for data *and* metadata, in a `0700` directory that is tightened if it pre-exists; a symlinked store path or entry file is refused; a failed metadata write leaves no data orphan; an interrupted or size-mismatched entry is never served. | `test_store_permissions_are_private`, `test_a_preexisting_loose_store_directory_is_tightened`, `test_symlinked_store_directory_is_refused`, `test_symlinked_metadata_is_not_followed`, `test_failed_metadata_write_leaves_no_orphan`, `test_truncated_write_is_not_served`, `test_data_without_meta_is_not_served`, `test_meta_without_data_is_not_served` |
| Bounds are enforced in code, with these defaults (each configurable): 32 MiB per entry, 128 MiB total, 2,000 entries, 168 h TTL; quota and count checked on every write; expired entries refused on read. | `test_entry_size_cap_refuses`, `test_quota_prunes_oldest_first`, `test_entry_count_is_bounded`, `test_ttl_expiry_deletes_and_reports`, `test_store_too_small_for_the_entry_returns_none` |
| Caller input never builds a filesystem path: ids are 128-bit random and validated against `^su_[0-9a-f]{32}$`. | `test_path_traversal_is_rejected`, `test_missing_and_malformed_ids` |
| Metadata and the stats log never contain tool output. Compression makes no network calls by default; optional OTLP telemetry is off unless `SUTRA_TELEMETRY=1`, carries no tool output, and uses bounded best-effort export. The stats log is opened `O_NOFOLLOW`. | `test_meta_does_not_contain_the_payload`, `test_a_symlinked_stats_file_is_not_appended_through` |
| Concurrent writes/reads and cleanup-during-read are safe: readers see a complete entry or an explicit failure. | `test_concurrent_puts_and_reads`, `test_cleanup_between_lookup_and_read_is_reported` |
| The request-stage retention memo is safe under concurrent sessions: one payload keeps one id under parallel calls, the table stays within `MAX_RETAINED`, and a second identical request is returned unchanged so a provider prefix cache is not invalidated. | `test_concurrent_requests_share_one_bounded_cache`, `test_the_shared_cache_evicts_old_payloads`, `test_a_second_identical_request_is_left_alone` |
| The stage runs inside a real host, not only in unit tests: Hermes discovers and loads the plugin through its own loader, its middleware dispatch returns the shortened request, the marker's stored copy is byte-identical to the original, and its observation hook receives the usage it is handed. | `test_the_host_dispatch_shortens_a_request_and_fires_the_usage_hook` |
| Retention keeps the store inside its ceiling when several sessions retain at once: the write and the budget sweep are one critical section, and a refused retention leaves the message byte-identical. | `test_concurrent_retention_stays_within_the_store_budget`, `test_a_refused_retention_leaves_the_message_untouched` |
| The store is **not encrypted**; any process running as the same OS user can read it. `SUTRA_STORE=0` disables retention entirely. | documented; per-profile isolation tested in `test_profiles_do_not_share_the_store` |

## 8. Failure behavior

| Guarantee | Enforced by |
|---|---|
| Compression never raises into the host: any internal error returns the original result and counts `fail-open`. | `test_compress_result_never_raises`, `test_deeply_nested_json_does_not_raise` |
| The middleware runs the tool exactly once, never swallows downstream errors, and never recompresses its own recovery tool's output. | `test_middleware_calls_next_exactly_once`, `test_downstream_errors_are_not_swallowed`, `test_middleware_does_not_recompress_its_own_evidence` |
| Configuration is parsed in one place per call; invalid values warn once and fall back to documented defaults; explicit `0` arguments are honored. | `test_invalid_env_values_fall_back_to_defaults`, `test_fail_open_is_honoured_from_the_resolved_config`, `test_explicit_zero_head_is_not_overridden_by_env` |

## 9. Observability

| Guarantee | Enforced by |
|---|---|
| `sutra.metrics_snapshot()` returns process-scoped counters (compression decisions, restore outcomes, cleanup, observed provider usage, request-stage rewrites per mode) plus current store size, and says so; the JSONL log is durable and records rewrites only. | `test_snapshot_is_explicitly_process_scoped_across_a_restart`, `test_snapshot_and_stats_log_agree_on_the_rewrite_subset` |
| `sutra stats` reads that log — the current file plus at most one rotated predecessor — and reports records, bytes before and after, saved percentage, the date range and a per-rule and per-tool table. A line it cannot parse is counted and the rest of the file still produces a number; a missing log is zero records, not an error; and the human report names the counters it cannot see because they live in the host process. | `test_the_log_aggregates_records_rules_and_tools`, `test_the_rotated_file_is_read_too`, `test_an_unreadable_line_is_counted_and_the_rest_still_parses`, `test_the_human_report_states_what_it_cannot_see`, `test_a_missing_log_is_zero_records_not_an_error` |
| Every request-stage decision is counted once for its mode (`requests`, `rewritten`, `unchanged`, `original_bytes`, `trimmed_bytes`, `saved_bytes`, and a `by_mode` table), so the depth a mode reaches can be measured on live traffic; a counter failure never breaks the request. | `test_a_rewrite_is_counted_with_its_bytes`, `test_each_mode_keeps_its_own_row`, `test_the_counter_never_raises_on_a_foreign_object`, `test_the_middleware_counts_the_request_it_rewrites` |
| A shortened request appends one content-free record to `<stats>-request.jsonl` (time, mode, session, before, after, saved) and an untouched request appends nothing; `sutra stats` reports the aggregate and names the log when it is empty, and the A/B runner attributes records to the session that sent them. | `test_a_shortened_request_is_written_with_its_session`, `test_a_request_left_alone_writes_nothing`, `test_the_stats_command_reports_the_request_stage`, `test_a_request_rewrite_is_attributed_to_its_session` |
| Request-stage shortening never removes a result the conversation still depends on: a result cited by a later message through its recovery id, or matched by a distinctive token (path, symbol, id-like name) that a later message also contains, stays byte-identical; a result nothing later refers to is still shortened. | `test_a_result_a_later_message_refers_to_is_never_trimmed`, `test_a_result_nothing_later_refers_to_is_still_trimmed`, `test_an_ordinary_word_is_not_a_dependency`, `test_dependency_protection_reads_the_responses_shape`, `test_the_system_prompt_is_not_a_dependency` |
| Provider usage is observed, never controlled: reported buckets are summed, a summary that did not arrive counts as unreported rather than zero, and the handler returns `None` without raising. | `test_reported_buckets_are_summed`, `test_a_missing_usage_block_counts_as_unreported`, `test_the_hook_never_raises_and_returns_none` |
| Metric failures never change a tool result or a restore. | `test_compression_metric_failure_never_breaks_the_result`, `test_metric_failures_never_break_the_tool` |

## 10. Explicitly not guaranteed

- **The visible copy is not the original.** Elision, repeat collapse, ANSI
  stripping and JSON re-encoding all change it; only the retained original is
  byte-exact, and only while the entry lives (TTL, quota).
- **Recoverability has a lifetime.** `expired` and `not_found` are normal,
  expected outcomes; the marker's retention window is the promise's duration.
- **No encryption, no multi-user ACL.** Filesystem permissions are the whole
  boundary (see SECURITY.md).
- **No live-model claims.** Token counts in the README are `tiktoken o200k_base`
  measurements of payload text, not bills; task success has not been measured
  against a live model (see benchmark.md, "Not measured").
- **Host-dependent visibility.** Whether the model sees `sutra_restore` depends
  on Hermes' toolset and Tool Search configuration (§6); SUTRA cannot force the
  host to surface it.
- **Search is literal, not semantic.** A natural-language query does not find
  related text. Ranked semantic retrieval is deferred with its evidence and
  revisit conditions in [semantic-retrieval.md](semantic-retrieval.md).

## Verification status

Every table row above cites the test or probe that fails if the guarantee
breaks. The suite runs on Python 3.11–3.14 in CI; the plugin manifest is
validated against the pinned Hermes revision. The end-to-end loop (real Hermes
gateway, real terminal tool, real middleware, real store, recovery through the
host's Tool Search bridge) was verified manually on 2026-10-24 against Hermes
Agent v0.22.2+3030.g4d04303; see the repository's baseline mission log for the
transcript-level evidence.

## OpenClaw adapter support

The SUTRA core is compatible with OpenClaw's generated plugin contract. OpenClaw
users can install the OpenClaw plugin via `sutra install --client openclaw`. Adapter
support is verified with scripted providers; live host testing is pending (see
integrations.md for per-environment status).
| The installed copy is compared with the package | `sutra doctor` reports `copy=stale` and the missing stages when the installed plugin predates this package, instead of passing it | `test_a_copy_without_the_request_stage_is_stale`, `test_changed_files_make_a_copy_stale`, `test_a_missing_manifest_is_unknown_not_current`, `test_the_formatter_names_the_missing_stage` |
| The proxy counts the usage the provider reported without changing the answer | A `/v1/messages` answer's `usage` is read (JSON, or the first event of a stream), its cache buckets are mapped onto the counters' names, and an answer without usage counts as not reported, not as zero; the client receives the provider's bytes unchanged | `test_a_json_answer_reports_its_usage_and_arrives_unchanged`, `test_a_streamed_answer_reports_the_usage_of_its_first_event`, `test_an_answer_without_usage_is_counted_as_unreported`, `test_the_counters_never_change_what_the_client_receives` |
| The proxy is a request boundary for a host without a hook | `sutra proxy` shortens a `POST` whose path ends in `/v1/messages` or `/chat/completions` — a host's base URL may add a prefix — and forwards everything else untouched, preserving credentials, paths and the response body, and falling back to the original bytes on any failure | `test_a_messages_request_is_shortened_and_the_key_survives_the_hop`, `test_an_openai_chat_body_is_shortened_on_the_plain_path`, `test_a_prefixed_chat_path_is_shortened_too`, `test_a_host_produced_result_is_retained_and_its_id_restores_the_bytes`, `test_a_non_messages_path_is_forwarded_untouched`, `test_unparsable_json_is_forwarded_untouched`, `test_a_streamed_response_arrives_unchanged`, `test_a_head_request_gets_headers_and_no_body`, `test_the_cli_serves_a_request_in_its_own_process`, `test_a_dead_upstream_reports_a_proxy_error_without_the_url` | `test_a_messages_request_is_shortened_and_the_key_survives_the_hop`, `test_a_host_produced_result_is_retained_and_its_id_restores_the_bytes`, `test_a_non_messages_path_is_forwarded_untouched`, `test_unparsable_json_is_forwarded_untouched`, `test_a_streamed_response_arrives_unchanged`, `test_a_head_request_gets_headers_and_no_body`, `test_the_cli_serves_a_request_in_its_own_process`, `test_a_dead_upstream_reports_a_proxy_error_without_the_url` |
| An Anthropic `tool_result` inside a user message is a result | the block's content is shortened and a marker quoted, `tool_use_id`/`is_error`/`cache_control` unchanged, only a single-result message may become a pointer | `test_a_tool_result_block_is_shortened_and_restorable`, `test_a_tool_result_block_of_parts_keeps_its_other_keys`, `test_a_repeated_tool_result_collapses_onto_the_first_copy`, `test_two_results_in_one_message_are_never_collapsed`, `test_an_anthropic_tool_result_body_is_shortened` |
| The OpenCode request hook shortens a result only when it can restore it | `sutra shorten-results` applies the same policy to bodies handed over one by one; a batch maps by position, and a refused retention leaves the text unchanged | `test_a_large_result_is_shortened_and_restorable`, `test_a_refused_retention_leaves_the_text_alone`, `test_the_batch_is_mapped_by_position`, `test_off_returns_the_same_texts`, `test_the_plugin_declares_the_request_hook` |
