# SUTRA v0.6.1 Performance Benchmarks

Generated: 2026-11-14T02:16:51+00:00
Total fixtures: 46
Real tool outputs: 46
Synthetic benchmarks: 0

## Overall Compression Results

| Metric | Before | After | Saved | Saved % |
|--------|--------|-------|-------|---------|
| **Total** | **503.5 kB** | **503.5 kB** | **0 B** | **0.0%** |

## Per-Tool Category Results (with Token Counts)

| Tool | Category | Cases | Original | Compressed | Saved | Saved % | Tokens Before | Tokens After | Tokens Saved | Tokens Saved % |
|------|----------|-------|----------|------------|-------|---------|---------------|--------------|---------------|-----------------|
| gcc | general/compiler_errors | 1 | 265 B | 265 B | 0 B | 0.0% | 67 | 67 | -198 | -295.5% |
| git | general/git_diff | 6 | 748 B | 748 B | 0 B | 0.0% | 260 | 260 | -488 | -187.7% |
| python3 (generated) | general/large_json | 1 | 480.2 kB | 480.2 kB | 0 B | 0.0% | 127,771 | 127,771 | -352,461 | -275.9% |
| node | general/long_stack_trace | 4 | 1.9 kB | 1.9 kB | 0 B | 0.0% | 587 | 587 | -1,332 | -226.9% |
| python3 | general/long_stack_trace | 5 | 1.5 kB | 1.5 kB | 0 B | 0.0% | 424 | 424 | -1,092 | -257.5% |
| pytest | general/repeated_test_output | 6 | 10.2 kB | 10.2 kB | 0 B | 0.0% | 2,349 | 2,349 | -7,810 | -332.5% |
| go (not installed) | go/compiler | 4 | 1.0 kB | 1.0 kB | 0 B | 0.0% | 320 | 320 | -717 | -224.1% |
| eslint | javascript/eslint | 3 | 627 B | 627 B | 0 B | 0.0% | 192 | 192 | -435 | -226.6% |
| jest | javascript/jest | 2 | 887 B | 887 B | 0 B | 0.0% | 306 | 306 | -581 | -189.9% |
| tsc | javascript/tsc | 3 | 591 B | 591 B | 0 B | 0.0% | 184 | 184 | -407 | -221.2% |
| vitest | javascript/vitest | 2 | 1.0 kB | 1.0 kB | 0 B | 0.0% | 444 | 444 | -604 | -136.0% |
| mypy | python/mypy | 2 | 385 B | 385 B | 0 B | 0.0% | 108 | 108 | -277 | -256.5% |
| pip | python/pip | 1 | 1.0 kB | 1.0 kB | 0 B | 0.0% | 237 | 237 | -787 | -332.1% |
| cargo | rust/cargo_check | 6 | 3.1 kB | 3.1 kB | 0 B | 0.0% | 955 | 955 | -2,114 | -221.4% |

## Individual Tool Results

| Case | Tool | Original | Compressed | Saved | Saved % | Rule |
|------|------|----------|------------|-------|---------|------|
| general/compiler_errors/gcc_type_error.txt | gcc | 265 B | 265 B | 0 B | 0.0% | `none` |
| general/git_diff/git_diff_staged.txt | git | 116 B | 116 B | 0 B | 0.0% | `none` |
| general/git_diff/git_diff_unstaged.txt | git | 118 B | 118 B | 0 B | 0.0% | `none` |
| general/git_status/git_log.txt | git | 30 B | 30 B | 0 B | 0.0% | `none` |
| general/git_status/git_status_clean.txt | git | 53 B | 53 B | 0 B | 0.0% | `none` |
| general/git_status/git_status_mixed.txt | git | 36 B | 36 B | 0 B | 0.0% | `none` |
| general/git_status/git_status_verbose.txt | git | 395 B | 395 B | 0 B | 0.0% | `none` |
| general/large_json/large_json_api_response.txt | python3 (generated) | 480.2 kB | 480.2 kB | 0 B | 0.0% | `json-kept` |
| general/long_stack_trace/node_async_stack.txt | node | 390 B | 390 B | 0 B | 0.0% | `none` |
| general/long_stack_trace/python_recursion_error.txt | python3 | 405 B | 405 B | 0 B | 0.0% | `none` |
| general/repeated_test_output/repeated_pytest_output.txt | pytest | 892 B | 892 B | 0 B | 0.0% | `none` |
| go/compiler/go_compiler_syntax_error.txt | go (not installed) | 294 B | 294 B | 0 B | 0.0% | `none` |
| go/compiler/go_compiler_type_error.txt | go (not installed) | 227 B | 227 B | 0 B | 0.0% | `none` |
| go/test/go_test_failing.txt | go (not installed) | 380 B | 380 B | 0 B | 0.0% | `none` |
| go/test/go_test_passing.txt | go (not installed) | 136 B | 136 B | 0 B | 0.0% | `none` |
| javascript/eslint/eslint_clean.txt | eslint | 0 B | 0 B | 0 B | 0.0% | `none` |
| javascript/eslint/eslint_errors.txt | eslint | 487 B | 487 B | 0 B | 0.0% | `none` |
| javascript/eslint/eslint_unused_variable.txt | eslint | 140 B | 140 B | 0 B | 0.0% | `none` |
| javascript/jest/jest_clean.txt | jest | 216 B | 216 B | 0 B | 0.0% | `none` |
| javascript/jest/jest_failing.txt | jest | 671 B | 671 B | 0 B | 0.0% | `none` |
| javascript/node/node_reference_error.txt | node | 582 B | 582 B | 0 B | 0.0% | `none` |
| javascript/node/node_uncaught_exception.txt | node | 632 B | 632 B | 0 B | 0.0% | `none` |
| javascript/node/node_unhandled_rejection.txt | node | 315 B | 315 B | 0 B | 0.0% | `none` |
| javascript/tsc/tsc_clean.txt | tsc | 0 B | 0 B | 0 B | 0.0% | `none` |
| javascript/tsc/tsc_strict_null.txt | tsc | 60 B | 60 B | 0 B | 0.0% | `none` |
| javascript/tsc/tsc_type_errors.txt | tsc | 531 B | 531 B | 0 B | 0.0% | `none` |
| javascript/vitest/vitest_clean.txt | vitest | 194 B | 194 B | 0 B | 0.0% | `none` |
| javascript/vitest/vitest_failing.txt | vitest | 854 B | 854 B | 0 B | 0.0% | `none` |
| python/mypy/mypy_clean.txt | mypy | 42 B | 42 B | 0 B | 0.0% | `none` |
| python/mypy/mypy_type_errors.txt | mypy | 343 B | 343 B | 0 B | 0.0% | `none` |
| python/pip/pip_install_fails.txt | pip | 1.0 kB | 1.0 kB | 0 B | 0.0% | `none` |
| python/pytest/pytest_collection_error.txt | pytest | 2.7 kB | 2.7 kB | 0 B | 0.0% | `none` |
| python/pytest/pytest_failing.txt | pytest | 2.4 kB | 2.4 kB | 0 B | 0.0% | `none` |
| python/pytest/pytest_passing.txt | pytest | 1.4 kB | 1.4 kB | 0 B | 0.0% | `none` |
| python/pytest/pytest_passing_warnings.txt | pytest | 1.5 kB | 1.5 kB | 0 B | 0.0% | `none` |
| python/pytest/pytest_skipped.txt | pytest | 1.3 kB | 1.3 kB | 0 B | 0.0% | `none` |
| python/traceback/python_importerror.txt | python3 | 185 B | 185 B | 0 B | 0.0% | `none` |
| python/traceback/python_nameerror.txt | python3 | 254 B | 254 B | 0 B | 0.0% | `none` |
| python/traceback/traceback_chained.txt | python3 | 508 B | 508 B | 0 B | 0.0% | `none` |
| python/traceback/traceback_valueerror.txt | python3 | 164 B | 164 B | 0 B | 0.0% | `none` |
| rust/cargo_check/cargo_check_clean.txt | cargo | 118 B | 118 B | 0 B | 0.0% | `none` |
| rust/cargo_check/cargo_check_deadcode.txt | cargo | 718 B | 718 B | 0 B | 0.0% | `none` |
| rust/cargo_check/cargo_check_type_error.txt | cargo | 408 B | 408 B | 0 B | 0.0% | `none` |
| rust/cargo_check/cargo_check_unused_import.txt | cargo | 481 B | 481 B | 0 B | 0.0% | `none` |
| rust/cargo_test/cargo_test_failing.txt | cargo | 705 B | 705 B | 0 B | 0.0% | `none` |
| rust/cargo_test/cargo_test_panic.txt | cargo | 639 B | 639 B | 0 B | 0.0% | `none` |
