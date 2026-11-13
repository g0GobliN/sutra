#!/usr/bin/env python3
"""Test script to verify the benchmark system works correctly.

This script runs basic validation of the benchmark system components
and ensures all requirements are met.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
def test_corpus_exists():
    """Test that the corpus.py file exists and can be imported."""
    print("Testing corpus.py...")
    try:
        from bench.corpus import get_corpus
        corpus = get_corpus()
        print(f"✓ Corpus loaded successfully: {len(corpus['fixtures'])} fixtures")
        return True
    except ImportError as e:
        print(f"✗ Failed to import corpus.py: {e}")
        return False
def test_run_benchmarks_script():
    """Test that run_benchmarks.py can be imported and has required functions."""
    print("Testing run_benchmarks.py...")
    try:
        # Check if the script exists
        script_path = ROOT / "bench" / "run_benchmarks.py"
        if not script_path.exists():
            print(f"✗ run_benchmarks.py not found at {script_path}")
            return False
        
        # Try to compile it
        with open(script_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        # Basic syntax check
        compile(source, str(script_path), 'exec')
        print("✓ run_benchmarks.py syntax is valid")
        return True
        
    except Exception as e:
        print(f"✗ Failed to validate run_benchmarks.py: {e}")
        return False
def test_benchmark_directory():
    """Test that the benchmark directory has all required files."""
    print("Testing benchmark directory structure...")
    required_files = [
        "bench/corpus.py",
        "bench/run_benchmarks.py",
        "bench/run_benchmarks.md",
        "bench/run_benchmarks.json",
    ]
    
    all_exist = True
    for file_path in required_files:
        full_path = ROOT / file_path
        if full_path.exists():
            print(f"✓ {file_path} exists")
        else:
            print(f"✗ {file_path} missing")
            all_exist = False
    
    return all_exist
def test_readme_updates():
    """Test that README.md has been updated with benchmark information."""
    print("Testing README.md updates...")
    readme_path = ROOT / "README.md"
    
    if not readme_path.exists():
        print(f"✗ README.md not found at {readme_path}")
        return False
    
    with open(readme_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check for benchmark-related content
    benchmark_keywords = [
        "Benchmarks",
        "run_benchmarks.py",
        "corpus.py",
        "Compression Ratio",
        "Latency (ms)",
        "Token Savings",
    ]
    
    all_present = True
    for keyword in benchmark_keywords:
        if keyword in content:
            print(f"✓ README.md contains '{keyword}'")
        else:
            print(f"✗ README.md missing '{keyword}'")
            all_present = False
    
    return all_present
def test_ci_workflow():
    """Test that CI workflow has been created."""
    print("Testing CI workflow...")
    workflow_path = ROOT / ".github" / "workflows" / "benchmark.yml"
    
    if not workflow_path.exists():
        print(f"✗ benchmark.yml not found at {workflow_path}")
        return False
    
    print(f"✓ CI workflow created at {workflow_path}")
    
    # Try to read the workflow file to check for key components
    with open(workflow_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    workflow_components = [
        "benchmark",
        "ubuntu-latest",
        "python",
        "actions/upload-artifact",
        "gh-pages",
    ]
    
    all_present = True
    for component in workflow_components:
        if component in content:
            print(f"✓ Workflow contains '{component}'")
        else:
            print(f"✗ Workflow missing '{component}'")
            all_present = False
    
    return all_present
def main():
    """Run all tests."""
    print("=" * 60)
    print("SUTRA v0.6.1 Benchmark System Validation")
    print("=" * 60)
    print()
    
    tests = [
        ("Corpus File", test_corpus_exists),
        ("Run Benchmarks Script", test_run_benchmarks_script),
        ("Benchmark Directory Structure", test_benchmark_directory),
        ("README.md Updates", test_readme_updates),
        ("CI Workflow", test_ci_workflow),
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        result = test_func()
        results.append((test_name, result))
        print()
    
    # Summary
    print("=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = 0
    failed = 0
    
    for test_name, result in results:
        status = "PASS" if result else "FAIL"
        icon = "✓" if result else "✗"
        print(f"{icon} {test_name}: {status}")
        if result:
            passed += 1
        else:
            failed += 1
    
    print(f"\nTotal: {passed + failed} tests")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    
    if failed == 0:
        print("\n🎉 All tests passed! The benchmark system is ready.")
        return 0
    else:
        print(f"\n❌ {failed} test(s) failed. Please fix the issues above.")
        return 1
if __name__ == "__main__":
    sys.exit(main())