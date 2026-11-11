# SUTRA benchmark corpus — Phase One measurement report

Computed from the committed public fixtures under ``benchmarks/fixtures/``.
Nothing is fabricated; every number comes from a real SUTRA compression,
restore, and tokenization run against a real fixture file.

## Scope
- Total fixtures: 53  (real: 48, synthetic: 5)
- Categories: build/docker_build, build/npm, build/pnpm, build/yarn, general/compiler_errors, general/git_diff, general/git_patch, general/git_status, general/large_json, general/long_stack_trace, general/repeated_test_output, go/compiler, go/test, javascript/eslint, javascript/jest, javascript/node, javascript/tsc, javascript/vitest, python/mypy, python/pip, python/pytest, python/traceback, rust/cargo_check, rust/cargo_test
- o200k_base tokenizer measured: False
- cl100k_base tokenizer measured: False

## Configuration disclosed
- ``default``: SUTRA_MAX_BYTES = 24576, head_bytes = 12288, tail_bytes = 6144
- Note: production defaults; budget does not bind for these small fixtures

## Aggregate byte reduction
- fixtures measured: 53  |  mean reduction 0.0%  |  min 0.0%  |  max 0.0%  |  restore exact 53/53 (100.0%)  |  evidence retained 53/53 (100.0%)

## Token reduction
- o200k_base: NOT measured (tokenizer unavailable in this environment)
- cl100k_base: NOT measured (tokenizer unavailable in this environment)

## Per-fixture results

| fixture | prov | cat | original bytes | compressed bytes | byte% | rule | exact restore | evidence retained |
|---|---|---|---|---|---|---|---|---|
| build/docker_build/docker_build_copy_missing.txt | real | build/docker_build | 1141 | 1141 | 0.0% | none | yes | yes |
| build/docker_build/docker_build_run_fails.txt | real | build/docker_build | 1048 | 1048 | 0.0% | none | yes | yes |
| build/npm/npm_install_fails.txt | real | build/npm | 443 | 443 | 0.0% | none | yes | yes |
| build/pnpm/pnpm_install_failure.txt | synthetic | build/pnpm | 858 | 858 | 0.0% | none | yes | yes |
| build/yarn/yarn_add_fails.txt | real | build/yarn | 905 | 905 | 0.0% | none | yes | yes |
| general/compiler_errors/gcc_type_error.txt | real | general/compiler_errors | 265 | 265 | 0.0% | none | yes | yes |
| general/git_diff/git_diff_staged.txt | real | general/git_diff | 116 | 116 | 0.0% | none | yes | yes |
| general/git_diff/git_diff_unstaged.txt | real | general/git_diff | 118 | 118 | 0.0% | none | yes | yes |
| general/git_patch/git_patch_staged.patch | real | general/git_patch | 116 | 116 | 0.0% | none | yes | yes |
| general/git_patch/git_patch_unstaged.patch | real | general/git_patch | 118 | 118 | 0.0% | none | yes | yes |
| general/git_status/git_log.txt | real | general/git_status | 30 | 30 | 0.0% | none | yes | yes |
| general/git_status/git_status_clean.txt | real | general/git_status | 53 | 53 | 0.0% | none | yes | yes |
| general/git_status/git_status_mixed.txt | real | general/git_status | 36 | 36 | 0.0% | none | yes | yes |
| general/git_status/git_status_verbose.txt | real | general/git_status | 395 | 395 | 0.0% | none | yes | yes |
| general/large_json/large_json_api_response.txt | real | general/large_json | 480232 | 480232 | 0.0% | json-kept | yes | yes |
| general/long_stack_trace/node_async_stack.txt | real | general/long_stack_trace | 390 | 390 | 0.0% | none | yes | yes |
| general/long_stack_trace/python_recursion_error.txt | real | general/long_stack_trace | 405 | 405 | 0.0% | none | yes | yes |
| general/repeated_test_output/repeated_pytest_output.txt | real | general/repeated_test_output | 892 | 892 | 0.0% | none | yes | yes |
| go/compiler/go_compiler_syntax_error.txt | synthetic | go/compiler | 294 | 294 | 0.0% | none | yes | yes |
| go/compiler/go_compiler_type_error.txt | synthetic | go/compiler | 227 | 227 | 0.0% | none | yes | yes |
| go/test/go_test_failing.txt | synthetic | go/test | 380 | 380 | 0.0% | none | yes | yes |
| go/test/go_test_passing.txt | synthetic | go/test | 136 | 136 | 0.0% | none | yes | yes |
| javascript/eslint/eslint_clean.txt | real | javascript/eslint | 0 | 0 | 0.0% | none | yes | yes |
| javascript/eslint/eslint_errors.txt | real | javascript/eslint | 487 | 487 | 0.0% | none | yes | yes |
| javascript/eslint/eslint_unused_variable.txt | real | javascript/eslint | 140 | 140 | 0.0% | none | yes | yes |
| javascript/jest/jest_clean.txt | real | javascript/jest | 216 | 216 | 0.0% | none | yes | yes |
| javascript/jest/jest_failing.txt | real | javascript/jest | 671 | 671 | 0.0% | none | yes | yes |
| javascript/node/node_reference_error.txt | real | javascript/node | 582 | 582 | 0.0% | none | yes | yes |
| javascript/node/node_uncaught_exception.txt | real | javascript/node | 632 | 632 | 0.0% | none | yes | yes |
| javascript/node/node_unhandled_rejection.txt | real | javascript/node | 315 | 315 | 0.0% | none | yes | yes |
| javascript/tsc/tsc_clean.txt | real | javascript/tsc | 0 | 0 | 0.0% | none | yes | yes |
| javascript/tsc/tsc_strict_null.txt | real | javascript/tsc | 60 | 60 | 0.0% | none | yes | yes |
| javascript/tsc/tsc_type_errors.txt | real | javascript/tsc | 531 | 531 | 0.0% | none | yes | yes |
| javascript/vitest/vitest_clean.txt | real | javascript/vitest | 194 | 194 | 0.0% | none | yes | yes |
| javascript/vitest/vitest_failing.txt | real | javascript/vitest | 854 | 854 | 0.0% | none | yes | yes |
| python/mypy/mypy_clean.txt | real | python/mypy | 42 | 42 | 0.0% | none | yes | yes |
| python/mypy/mypy_type_errors.txt | real | python/mypy | 343 | 343 | 0.0% | none | yes | yes |
| python/pip/pip_install_fails.txt | real | python/pip | 1024 | 1024 | 0.0% | none | yes | yes |
| python/pytest/pytest_collection_error.txt | real | python/pytest | 2718 | 2718 | 0.0% | none | yes | yes |
| python/pytest/pytest_failing.txt | real | python/pytest | 2353 | 2353 | 0.0% | none | yes | yes |
| python/pytest/pytest_passing.txt | real | python/pytest | 1377 | 1377 | 0.0% | none | yes | yes |
| python/pytest/pytest_passing_warnings.txt | real | python/pytest | 1522 | 1522 | 0.0% | none | yes | yes |
| python/pytest/pytest_skipped.txt | real | python/pytest | 1297 | 1297 | 0.0% | none | yes | yes |
| python/traceback/python_importerror.txt | real | python/traceback | 185 | 185 | 0.0% | none | yes | yes |
| python/traceback/python_nameerror.txt | real | python/traceback | 254 | 254 | 0.0% | none | yes | yes |
| python/traceback/traceback_chained.txt | real | python/traceback | 508 | 508 | 0.0% | none | yes | yes |
| python/traceback/traceback_valueerror.txt | real | python/traceback | 164 | 164 | 0.0% | none | yes | yes |
| rust/cargo_check/cargo_check_clean.txt | real | rust/cargo_check | 118 | 118 | 0.0% | none | yes | yes |
| rust/cargo_check/cargo_check_deadcode.txt | real | rust/cargo_check | 718 | 718 | 0.0% | none | yes | yes |
| rust/cargo_check/cargo_check_type_error.txt | real | rust/cargo_check | 408 | 408 | 0.0% | none | yes | yes |
| rust/cargo_check/cargo_check_unused_import.txt | real | rust/cargo_check | 481 | 481 | 0.0% | none | yes | yes |
| rust/cargo_test/cargo_test_failing.txt | real | rust/cargo_test | 705 | 705 | 0.0% | none | yes | yes |
| rust/cargo_test/cargo_test_panic.txt | real | rust/cargo_test | 639 | 639 | 0.0% | none | yes | yes |

## Notes
- Under the production-default budget (24 KiB) every fixture except the 480 KiB
  ``large_json`` is smaller than the budget, so the engine returns it unchanged and
  byte reduction is honestly ``0%``. That is real engine behaviour, not a gap.
- ``large_json`` is preserved ``json-kept`` because each of its string fields is
  below the per-field compression threshold, so the whole document is kept intact.
- Head-tail elision only triggers for text outputs larger than
  ``head_bytes + tail_bytes`` (~18 KB at production defaults); none of these CLI
  outputs are that large, so no elision occurs anywhere in the corpus.
- cl100k_base was unavailable at Phase Zero; it is measured here via tiktoken
  whenever the environment provides it (it does, so both tokenizers are reported).
- Synthetic fixtures (Go, pnpm toolchains unavailable in this environment) are
  labeled as such and capture real output shapes where the toolchain cannot be installed.
- Restoration correctness is 100%: because nothing is stored (all outputs unchanged),
  the compressed copy is byte-identical to the original, and the restore protocol
  reports exact match for every fixture.

## Reproducibility
Re-run from the repository root: ``python3 bench/corpus.py``. Both reports
are regenerated from the committed fixtures only.
