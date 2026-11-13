# SUTRA v0.6.1 Performance Benchmarks

Generated: 2026-11-11T23:56:57+00:00
Total fixtures: 53
Real tool outputs: 53
Synthetic benchmarks: 0

## Overall Compression Results

| Metric | Before | After | Saved | Saved % |
|--------|--------|-------|-------|---------|
| **Total** | **5.3 kB** | **5.3 kB** | **0 B** | **0.0%** |

## Per-Tool Category Results (with Token Counts)

| Tool | Category | Cases | Original | Compressed | Saved | Saved % | Tokens Before | Tokens After | Tokens Saved | Tokens Saved % |
|------|----------|-------|----------|------------|-------|---------|---------------|--------------|---------------|-----------------|
| docker | build/docker_build | 2 | 64 B | 64 B | 0 B | 0.0% | 13 | 13 | -51 | -392.3% |
| npm | build/npm | 1 | 89 B | 89 B | 0 B | 0.0% | 18 | 18 | -71 | -394.4% |
| pnpm (binary broken at runtime) | build/pnpm | 1 | 57 B | 57 B | 0 B | 0.0% | 15 | 15 | -42 | -280.0% |
| yarn | build/yarn | 1 | 89 B | 89 B | 0 B | 0.0% | 18 | 18 | -71 | -394.4% |
| gcc | general/compiler_errors | 1 | 43 B | 43 B | 0 B | 0.0% | 10 | 10 | -33 | -330.0% |
| git | general/git_diff | 8 | 853 B | 853 B | 0 B | 0.0% | 271 | 271 | -582 | -214.8% |
| python3 (generated) | general/large_json | 1 | 122 B | 122 B | 0 B | 0.0% | 38 | 38 | -84 | -221.1% |
| node | general/long_stack_trace | 4 | 312 B | 312 B | 0 B | 0.0% | 104 | 104 | -208 | -200.0% |
| python3 | general/long_stack_trace | 5 | 610 B | 610 B | 0 B | 0.0% | 190 | 190 | -420 | -221.1% |
| pytest | general/repeated_test_output | 6 | 441 B | 441 B | 0 B | 0.0% | 128 | 128 | -313 | -244.5% |
| go (not installed) | go/compiler | 4 | 136 B | 136 B | 0 B | 0.0% | 44 | 44 | -92 | -209.1% |
| eslint | javascript/eslint | 3 | 423 B | 423 B | 0 B | 0.0% | 129 | 129 | -294 | -227.9% |
| jest | javascript/jest | 2 | 209 B | 209 B | 0 B | 0.0% | 53 | 53 | -156 | -294.3% |
| tsc | javascript/tsc | 3 | 423 B | 423 B | 0 B | 0.0% | 129 | 129 | -294 | -227.9% |
| vitest | javascript/vitest | 2 | 189 B | 189 B | 0 B | 0.0% | 46 | 46 | -143 | -310.9% |
| mypy | python/mypy | 2 | 86 B | 86 B | 0 B | 0.0% | 20 | 20 | -66 | -330.0% |
| pip | python/pip | 1 | 43 B | 43 B | 0 B | 0.0% | 10 | 10 | -33 | -330.0% |
| cargo | rust/cargo_check | 6 | 1.1 kB | 1.1 kB | 0 B | 0.0% | 408 | 408 | -720 | -176.5% |

## Individual Tool Results

| Case | Tool | Original | Compressed | Saved | Saved % | Rule |
|------|------|----------|------------|-------|---------|------|
| build/docker_build/docker_build_copy_missing.txt | docker | 28 B | 28 B | 0 B | 0.0% | `none` |
| build/docker_build/docker_build_run_fails.txt | docker | 36 B | 36 B | 0 B | 0.0% | `none` |
| build/npm/npm_install_fails.txt | npm | 89 B | 89 B | 0 B | 0.0% | `none` |
| build/pnpm/pnpm_install_failure.txt | pnpm (binary broken at runtime) | 57 B | 57 B | 0 B | 0.0% | `none` |
| build/yarn/yarn_add_fails.txt | yarn | 89 B | 89 B | 0 B | 0.0% | `none` |
| general/compiler_errors/gcc_type_error.txt | gcc | 43 B | 43 B | 0 B | 0.0% | `none` |
| general/git_diff/git_diff_staged.txt | git | 130 B | 130 B | 0 B | 0.0% | `none` |
| general/git_diff/git_diff_unstaged.txt | git | 130 B | 130 B | 0 B | 0.0% | `none` |
| general/git_patch/git_patch_staged.patch | git | 130 B | 130 B | 0 B | 0.0% | `none` |
| general/git_patch/git_patch_unstaged.patch | git | 130 B | 130 B | 0 B | 0.0% | `none` |
| general/git_status/git_log.txt | git | 39 B | 39 B | 0 B | 0.0% | `none` |
| general/git_status/git_status_clean.txt | git | 98 B | 98 B | 0 B | 0.0% | `none` |
| general/git_status/git_status_mixed.txt | git | 98 B | 98 B | 0 B | 0.0% | `none` |
| general/git_status/git_status_verbose.txt | git | 98 B | 98 B | 0 B | 0.0% | `none` |
| general/large_json/large_json_api_response.txt | python3 (generated) | 122 B | 122 B | 0 B | 0.0% | `none` |
| general/long_stack_trace/node_async_stack.txt | node | 78 B | 78 B | 0 B | 0.0% | `none` |
| general/long_stack_trace/python_recursion_error.txt | python3 | 122 B | 122 B | 0 B | 0.0% | `none` |
| general/repeated_test_output/repeated_pytest_output.txt | pytest | 69 B | 69 B | 0 B | 0.0% | `none` |
| go/compiler/go_compiler_syntax_error.txt | go (not installed) | 34 B | 34 B | 0 B | 0.0% | `none` |
| go/compiler/go_compiler_type_error.txt | go (not installed) | 34 B | 34 B | 0 B | 0.0% | `none` |
| go/test/go_test_failing.txt | go (not installed) | 34 B | 34 B | 0 B | 0.0% | `none` |
| go/test/go_test_passing.txt | go (not installed) | 34 B | 34 B | 0 B | 0.0% | `none` |
| javascript/eslint/eslint_clean.txt | eslint | 141 B | 141 B | 0 B | 0.0% | `none` |
| javascript/eslint/eslint_errors.txt | eslint | 141 B | 141 B | 0 B | 0.0% | `none` |
| javascript/eslint/eslint_unused_variable.txt | eslint | 141 B | 141 B | 0 B | 0.0% | `none` |
| javascript/jest/jest_clean.txt | jest | 86 B | 86 B | 0 B | 0.0% | `none` |
| javascript/jest/jest_failing.txt | jest | 123 B | 123 B | 0 B | 0.0% | `none` |
| javascript/node/node_reference_error.txt | node | 78 B | 78 B | 0 B | 0.0% | `none` |
| javascript/node/node_uncaught_exception.txt | node | 78 B | 78 B | 0 B | 0.0% | `none` |
| javascript/node/node_unhandled_rejection.txt | node | 78 B | 78 B | 0 B | 0.0% | `none` |
| javascript/tsc/tsc_clean.txt | tsc | 141 B | 141 B | 0 B | 0.0% | `none` |
| javascript/tsc/tsc_strict_null.txt | tsc | 141 B | 141 B | 0 B | 0.0% | `none` |
| javascript/tsc/tsc_type_errors.txt | tsc | 141 B | 141 B | 0 B | 0.0% | `none` |
| javascript/vitest/vitest_clean.txt | vitest | 81 B | 81 B | 0 B | 0.0% | `none` |
| javascript/vitest/vitest_failing.txt | vitest | 108 B | 108 B | 0 B | 0.0% | `none` |
| python/mypy/mypy_clean.txt | mypy | 43 B | 43 B | 0 B | 0.0% | `none` |
| python/mypy/mypy_type_errors.txt | mypy | 43 B | 43 B | 0 B | 0.0% | `none` |
| python/pip/pip_install_fails.txt | pip | 43 B | 43 B | 0 B | 0.0% | `none` |
| python/pytest/pytest_collection_error.txt | pytest | 69 B | 69 B | 0 B | 0.0% | `none` |
| python/pytest/pytest_failing.txt | pytest | 96 B | 96 B | 0 B | 0.0% | `none` |
| python/pytest/pytest_passing.txt | pytest | 69 B | 69 B | 0 B | 0.0% | `none` |
| python/pytest/pytest_passing_warnings.txt | pytest | 69 B | 69 B | 0 B | 0.0% | `none` |
| python/pytest/pytest_skipped.txt | pytest | 69 B | 69 B | 0 B | 0.0% | `none` |
| python/traceback/python_importerror.txt | python3 | 122 B | 122 B | 0 B | 0.0% | `none` |
| python/traceback/python_nameerror.txt | python3 | 122 B | 122 B | 0 B | 0.0% | `none` |
| python/traceback/traceback_chained.txt | python3 | 122 B | 122 B | 0 B | 0.0% | `none` |
| python/traceback/traceback_valueerror.txt | python3 | 122 B | 122 B | 0 B | 0.0% | `none` |
| rust/cargo_check/cargo_check_clean.txt | cargo | 188 B | 188 B | 0 B | 0.0% | `none` |
| rust/cargo_check/cargo_check_deadcode.txt | cargo | 188 B | 188 B | 0 B | 0.0% | `none` |
| rust/cargo_check/cargo_check_type_error.txt | cargo | 188 B | 188 B | 0 B | 0.0% | `none` |
| rust/cargo_check/cargo_check_unused_import.txt | cargo | 188 B | 188 B | 0 B | 0.0% | `none` |
| rust/cargo_test/cargo_test_failing.txt | cargo | 188 B | 188 B | 0 B | 0.0% | `none` |
| rust/cargo_test/cargo_test_panic.txt | cargo | 188 B | 188 B | 0 B | 0.0% | `none` |
