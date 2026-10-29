# The correctness contract

What SUTRA guarantees for every tool result it touches, what it explicitly does
not guarantee, and which tests enforce each claim. Every statement here describes
verified behavior — code plus the named tests — not intent. Where a guarantee
depends on the host (Hermes), the dependency is named.

Related: [recovery.md](recovery.md) (the retrieval protocol in full),
[benchmark.md](benchmark.md) (what is measured and what is not),
[SECURITY.md](SECURITY.md) (the security model), [../README.md](../README.md)
(operation and configuration).

## 1. Scope and pass-through

| Guarantee | Enforced by |
|---|---|
| A tool result of any type is returned as the same type. `str` in → `str` out; `dict` in → `dict` out with the same key set; anything else (`None`, numbers, lists, bytes, objects) is returned unchanged. | `test_unknown_result_shapes_pass_through`, `test_json_envelope_keeps_its_top_level_type_and_keys` |
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
| A payload that *is or contains* a patch — `diff --git`, `--- `/`+++ ` header pairs, `@@ ` hunks, or plain `diff` output (change command + `<`/`>` rows) — is returned byte-identical (`diff-kept`). One marker line anywhere protects the whole payload; this is deliberate conservatism, priced visibly in the benchmark (the 600 KB patch row saves 0%). | `test_diff_is_never_rewritten`, `test_plain_unified_diff_without_git_prefix_is_never_rewritten`, `test_plain_diff_output_is_never_rewritten`, `test_a_patch_fragment_protects_the_whole_payload` |
| A traceback keeps its error end: at least 1 KiB of tail, at most 2 KiB of head, inside the same budget. | `test_traceback_keeps_the_error_end`, `test_traceback_respects_max_bytes` |
| A payload that is empty after ANSI stripping is not rewritten. | probe P2 (2026-10-24) |
| Text fidelity: CRLF and the final newline survive; cuts never split a UTF-8 character; repeat collapse removes whole lines and preserves terminators. | `test_crlf_and_final_newline_are_preserved`, `test_missing_final_newline_is_preserved`, `test_unicode_cut_does_not_produce_replacement_characters`, `test_collapse_repeats_preserves_line_endings` |

## 4. Markers — the machine-readable surface

Every rewrite carries one single-line marker stating: completeness (`partial`),
the omitted byte count and reason, the JSON pointer when the elision is inside a
field, the retention id, the retention window, and an exact `sutra_restore` call
for the omitted range (byte offsets) or the omitted lines (repeat collapse).

| Guarantee | Enforced by |
|---|---|
| The marker is the only place omission is recorded, and it never claims bytes it did not keep: no stored original → no id, and the marker says "original not retained" with re-run advice. | `test_disabled_store_marks_the_result_as_not_retained`, `test_every_marker_renders_exactly` |
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
| Large arrays with no large string fields are returned untouched (`json-kept`) — the price of keeping their shape. | benchmark row "8,000-item JSON array" |
| `SUTRA_JSON_ITEMS` is retired: parsed, warned about once, ignored. | `test_json_retired_array_option_is_ignored` |

## 6. Recovery (`sutra_restore`)

Full protocol: [recovery.md](recovery.md). The guarantees:

| Guarantee | Enforced by |
|---|---|
| Paged `mode=bytes` is byte-exact: concatenating successive pages reproduces the original slice, multi-byte text included; paging stops on UTF-8 boundaries. | `test_bytes_mode_pages_out_the_exact_original_slice` |
| Every request returns an explicit status — `ok`, `not_found`, `expired`, `too_large`, `unavailable`, `error` — and never raises into the agent loop. | `test_restore_never_raises`, `test_missing_and_malformed_ids`, `test_invalid_mode_is_an_error` |
| Responses are bounded: 8 KiB default, 64 KiB hard cap, ≤20 matches, ≤200-char literal query, no regex. | `test_lines_mode_is_bounded`, `test_search_respects_max_matches_and_reports_more`, `test_query_length_is_bounded` |
| `mode=full` reports `sha256_verified` from the store's own digest check over exactly the bytes returned. | `test_full_mode_verifies_the_hash`, `test_full_mode_uses_the_store_verifier`, `test_full_mode_reports_a_corrupted_entry` |
| Field-scoped requests resolve RFC 6901 pointers against the stored original and serve that field's bytes; unresolvable pointers error without content. | `test_field_scope_returns_the_exact_field_text`, `test_unresolvable_field_pointer_errors_without_content` |
| Every issued id resolves to the original it was issued for, or to an explicit failure (`not_found`/`expired`) — never to a different payload. | `test_end_to_end_journey`, `test_profiles_do_not_share_the_store` |
| Host dependency: the tool is visible to the model only when the `sutra` toolset is enabled for the platform. With Hermes' Tool Search active, plugin tools may additionally be **deferred** behind the `tool_search`/`tool_describe`/`tool_call` bridge; the tool still exists and dispatches, but a model may need to search for it first. Operators can keep it eager via Hermes' `tools.tool_search` defer configuration. | verified against Hermes v0.22.2+3030.g4d04303 (2026-10-24, see baseline mission log); not testable without the host |

## 7. Storage and security

Full model: [SECURITY.md](SECURITY.md). The guarantees:

| Guarantee | Enforced by |
|---|---|
| Originals are written atomically (temp + rename), `0600`, `O_NOFOLLOW` for data *and* metadata, in a `0700` directory that is tightened if it pre-exists; a symlinked store path or entry file is refused; a failed metadata write leaves no data orphan; an interrupted or size-mismatched entry is never served. | `test_store_permissions_are_private`, `test_a_preexisting_loose_store_directory_is_tightened`, `test_symlinked_store_directory_is_refused`, `test_symlinked_metadata_is_not_followed`, `test_failed_metadata_write_leaves_no_orphan`, `test_truncated_write_is_not_served`, `test_data_without_meta_is_not_served`, `test_meta_without_data_is_not_served` |
| Bounds are enforced in code: 32 MiB per entry, 128 MiB total, 2,000 entries, 168 h TTL; quota and count checked on every write; expired entries refused on read. | `test_entry_size_cap_refuses`, `test_quota_prunes_oldest_first`, `test_entry_count_is_bounded`, `test_ttl_expiry_deletes_and_reports`, `test_store_too_small_for_the_entry_returns_none` |
| Caller input never builds a filesystem path: ids are 128-bit random and validated against `^su_[0-9a-f]{32}$`. | `test_path_traversal_is_rejected`, `test_missing_and_malformed_ids` |
| Metadata and the stats log never contain tool output; nothing leaves the machine; no network code exists. The stats log is opened `O_NOFOLLOW`. | `test_meta_does_not_contain_the_payload`, `test_a_symlinked_stats_file_is_not_appended_through` |
| Concurrent writes/reads and cleanup-during-read are safe: readers see a complete entry or an explicit failure. | `test_concurrent_puts_and_reads`, `test_cleanup_between_lookup_and_read_is_reported` |
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
| `sutra.metrics_snapshot()` returns process-scoped counters (compression decisions, restore outcomes, cleanup) plus current store size, and says so; the JSONL log is durable and records rewrites only. | `test_snapshot_is_explicitly_process_scoped_across_a_restart`, `test_snapshot_and_stats_log_agree_on_the_rewrite_subset` |
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

## Verification status

Every table row above cites the test or probe that fails if the guarantee
breaks. The suite runs on Python 3.11–3.13 in CI; the plugin manifest is
validated against the pinned Hermes revision. The end-to-end loop (real Hermes
gateway, real terminal tool, real middleware, real store, recovery through the
host's Tool Search bridge) was verified manually on 2026-10-24 against Hermes
Agent v0.22.2+3030.g4d04303; see the repository's baseline mission log for the
transcript-level evidence.
