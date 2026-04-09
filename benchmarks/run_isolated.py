#!/usr/bin/env python3
"""
Subprocess-isolated benchmark runner for xmltodict.

Each measurement runs in a fresh Python subprocess to eliminate JIT warming,
memory fragmentation, and inter-benchmark interference.

Usage:
    # Generate fixtures first (if not already done):
    python benchmarks/generate_fixtures.py

    # Run all benchmarks (20 subprocess runs each, median reported):
    python benchmarks/run_isolated.py --save benchmarks/results/baseline.json --python-only
    python benchmarks/run_isolated.py --save benchmarks/results/rs_version.json

    # Fewer runs for a quick check:
    python benchmarks/run_isolated.py --runs 5

    # Compare results:
    python benchmarks/compare.py benchmarks/results/
"""

import argparse
import json
import os
import subprocess
import sys
import textwrap

BENCHMARKS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BENCHMARKS_DIR)
FIXTURES_DIR = os.path.join(BENCHMARKS_DIR, "fixtures")

DEFAULT_RUNS = 20

# ── Single-measurement scripts (run in subprocess) ──────────────────────────

PARSE_THROUGHPUT_SCRIPT = textwrap.dedent("""\
    import gc, json, os, sys, time
    sys.path.insert(0, {repo_root!r})
    import xmltodict
    {backend_setup}
    data = open({fixture_path!r}, "rb").read()
    size_mb = len(data) / (1024 * 1024)
    # warm up once
    xmltodict.parse(data)
    # timed run
    gc.collect(); gc.disable()
    t0 = time.perf_counter()
    xmltodict.parse(data)
    t1 = time.perf_counter()
    gc.enable()
    elapsed = t1 - t0
    print(json.dumps({{"throughput_mbs": size_mb / elapsed, "size_mb": size_mb}}))
""")

PER_ELEMENT_SCRIPT = textwrap.dedent("""\
    import gc, json, os, sys, time
    sys.path.insert(0, {repo_root!r})
    import xmltodict
    {backend_setup}
    n = {n}
    parts = [b'<?xml version="1.0"?>\\n<root>\\n']
    for i in range(n):
        parts.append(f'  <item id="{{i}}"><v>{{i}}</v></item>\\n'.encode())
    parts.append(b"</root>")
    data = b"".join(parts)
    # warm up
    xmltodict.parse(data)
    # timed
    gc.collect(); gc.disable()
    t0 = time.perf_counter()
    xmltodict.parse(data)
    t1 = time.perf_counter()
    gc.enable()
    us_per_elem = ((t1 - t0) * 1e6) / n
    print(json.dumps({{"us_per_element": us_per_elem}}))
""")

MEMORY_RATIO_SCRIPT = textwrap.dedent("""\
    import gc, json, os, sys, tracemalloc
    sys.path.insert(0, {repo_root!r})
    import xmltodict
    {backend_setup}
    data = open({fixture_path!r}, "rb").read()
    size_mb = len(data) / (1024 * 1024)
    gc.collect()
    tracemalloc.start()
    xmltodict.parse(data)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_mb = peak / (1024 * 1024)
    print(json.dumps({{"size_mb": size_mb, "peak_mb": peak_mb, "ratio": peak_mb / size_mb if size_mb > 0 else 0}}))
""")

STREAMING_MEMORY_SCRIPT = textwrap.dedent("""\
    import gc, json, os, sys, tracemalloc
    sys.path.insert(0, {repo_root!r})
    import xmltodict
    {backend_setup}
    data = open({fixture_path!r}, "rb").read()
    size_mb = len(data) / (1024 * 1024)
    def _noop(path, item): return True
    gc.collect()
    tracemalloc.start()
    xmltodict.parse(data, item_depth=2, item_callback=_noop)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    overhead_kb = peak / 1024
    print(json.dumps({{"size_mb": size_mb, "overhead_kb": overhead_kb}}))
""")

UNPARSE_THROUGHPUT_SCRIPT = textwrap.dedent("""\
    import gc, json, os, sys, time
    sys.path.insert(0, {repo_root!r})
    import xmltodict
    {backend_setup}
    data = open({fixture_path!r}, "rb").read()
    parsed = xmltodict.parse(data)
    output_xml = xmltodict.unparse(parsed)
    output_mb = len(output_xml.encode()) / (1024 * 1024)
    # warm up
    xmltodict.unparse(parsed)
    # timed
    gc.collect(); gc.disable()
    t0 = time.perf_counter()
    xmltodict.unparse(parsed)
    t1 = time.perf_counter()
    gc.enable()
    elapsed = t1 - t0
    print(json.dumps({{"output_mb": output_mb, "throughput_mbs": output_mb / elapsed}}))
""")

# ── Helpers ──────────────────────────────────────────────────────────────────

def _backend_setup(python_only: bool) -> str:
    if python_only:
        return "xmltodict._RUST_AVAILABLE = False; xmltodict._BACKEND = 'python'"
    return ""


def _run_subprocess(script: str) -> dict:
    """Run a Python script in a fresh subprocess and parse JSON output."""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Subprocess failed:\n{result.stderr}")
    return json.loads(result.stdout.strip())


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    return s[n // 2]


def _run_n_times(script: str, key: str, n_runs: int, label: str) -> float:
    """Run script n_runs times in fresh subprocesses, return median of key."""
    values = []
    for i in range(n_runs):
        try:
            result = _run_subprocess(script)
            values.append(result[key])
        except Exception as e:
            print(f"    [WARN] run {i+1}/{n_runs} failed for {label}: {e}", file=sys.stderr)
    if not values:
        raise RuntimeError(f"All {n_runs} runs failed for {label}")
    return _median(values)


def _progress(label: str, current: int, total: int):
    """Print progress indicator."""
    pct = current / total * 100
    print(f"  [{current:>2}/{total}] {label}... ", end="", flush=True)


# ── Benchmark sections ──────────────────────────────────────────────────────

def bench_parse_throughput(n_runs: int, python_only: bool) -> dict:
    print(f"\n{'='*60}")
    print(f"  PARSE throughput  ({n_runs} subprocess runs, median)")
    print(f"{'='*60}")

    fixtures = ["small.xml", "medium.xml", "large.xml", "wide.xml", "deep.xml", "namespaced.xml"]
    results = {}
    backend = _backend_setup(python_only)

    for i, fx in enumerate(fixtures):
        path = os.path.join(FIXTURES_DIR, fx)
        if not os.path.exists(path):
            print(f"  [SKIP] {fx} not found")
            continue

        _progress(fx, i + 1, len(fixtures))
        script = PARSE_THROUGHPUT_SCRIPT.format(
            repo_root=REPO_ROOT, fixture_path=path, backend_setup=backend,
        )

        # Run n_runs subprocesses, collect throughput values
        throughputs = []
        size_mb = None
        for _ in range(n_runs):
            r = _run_subprocess(script)
            throughputs.append(r["throughput_mbs"])
            size_mb = r["size_mb"]

        med = _median(throughputs)
        results[fx] = {"size_mb": round(size_mb, 3), "throughput_mbs": round(med, 2)}
        print(f"{med:.1f} MB/s  (range: {min(throughputs):.1f}–{max(throughputs):.1f})")

    return results


def bench_per_element(n_runs: int, python_only: bool) -> dict:
    print(f"\n{'='*60}")
    print(f"  PER-ELEMENT cost  ({n_runs} subprocess runs, median)")
    print(f"{'='*60}")

    counts = [100, 1_000, 5_000, 10_000, 50_000]
    results = {}
    backend = _backend_setup(python_only)

    for i, n in enumerate(counts):
        _progress(f"N={n:,}", i + 1, len(counts))
        script = PER_ELEMENT_SCRIPT.format(
            repo_root=REPO_ROOT, n=n, backend_setup=backend,
        )

        values = []
        for _ in range(n_runs):
            r = _run_subprocess(script)
            values.append(r["us_per_element"])

        med = _median(values)
        results[str(n)] = round(med, 4)
        print(f"{med:.2f} µs/elem  (range: {min(values):.2f}–{max(values):.2f})")

    return results


def bench_memory(n_runs: int, python_only: bool) -> dict:
    print(f"\n{'='*60}")
    print(f"  MEMORY ratio  ({n_runs} subprocess runs, median)")
    print(f"{'='*60}")

    fixtures = ["medium.xml", "large.xml", "wide.xml"]
    results = {}
    backend = _backend_setup(python_only)

    # Non-streaming
    print("  Non-streaming:")
    for i, fx in enumerate(fixtures):
        path = os.path.join(FIXTURES_DIR, fx)
        if not os.path.exists(path):
            continue

        _progress(fx, i + 1, len(fixtures))
        script = MEMORY_RATIO_SCRIPT.format(
            repo_root=REPO_ROOT, fixture_path=path, backend_setup=backend,
        )

        ratios = []
        size_mb = peak_mb = 0
        for _ in range(n_runs):
            r = _run_subprocess(script)
            ratios.append(r["ratio"])
            size_mb = r["size_mb"]
            peak_mb = r["peak_mb"]

        med_ratio = _median(ratios)
        # Use the median ratio to derive a consistent peak_mb
        med_peak = med_ratio * size_mb
        results[f"{fx}_non_streaming"] = {
            "size_mb": round(size_mb, 3),
            "peak_mb": round(med_peak, 3),
            "ratio": round(med_ratio, 2),
        }
        print(f"{med_ratio:.1f}x  (range: {min(ratios):.1f}–{max(ratios):.1f})")

    # Streaming
    print("  Streaming:")
    for i, fx in enumerate(fixtures):
        path = os.path.join(FIXTURES_DIR, fx)
        if not os.path.exists(path):
            continue

        _progress(fx, i + 1, len(fixtures))
        script = STREAMING_MEMORY_SCRIPT.format(
            repo_root=REPO_ROOT, fixture_path=path, backend_setup=backend,
        )

        overheads = []
        size_mb = 0
        for _ in range(n_runs):
            r = _run_subprocess(script)
            overheads.append(r["overhead_kb"])
            size_mb = r["size_mb"]

        med = _median(overheads)
        results[f"{fx}_streaming"] = {
            "size_mb": round(size_mb, 3),
            "overhead_kb": round(med, 1),
        }
        print(f"{med:.0f} KB  (range: {min(overheads):.0f}–{max(overheads):.0f})")

    return results


def bench_unparse_throughput(n_runs: int, python_only: bool) -> dict:
    print(f"\n{'='*60}")
    print(f"  UNPARSE throughput  ({n_runs} subprocess runs, median)")
    print(f"{'='*60}")

    fixtures = ["medium.xml", "large.xml", "wide.xml"]
    results = {}
    backend = _backend_setup(python_only)

    for i, fx in enumerate(fixtures):
        path = os.path.join(FIXTURES_DIR, fx)
        if not os.path.exists(path):
            continue

        _progress(fx, i + 1, len(fixtures))
        script = UNPARSE_THROUGHPUT_SCRIPT.format(
            repo_root=REPO_ROOT, fixture_path=path, backend_setup=backend,
        )

        throughputs = []
        output_mb = 0
        for _ in range(n_runs):
            r = _run_subprocess(script)
            throughputs.append(r["throughput_mbs"])
            output_mb = r["output_mb"]

        med = _median(throughputs)
        results[fx] = {"output_mb": round(output_mb, 3), "throughput_mbs": round(med, 2)}
        print(f"{med:.1f} MB/s  (range: {min(throughputs):.1f}–{max(throughputs):.1f})")

    return results


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Subprocess-isolated xmltodict benchmarks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--runs", type=int, default=DEFAULT_RUNS,
        help=f"Number of subprocess runs per measurement (default: {DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--save", metavar="FILE",
        help="Save results to JSON file",
    )
    parser.add_argument(
        "--python-only", action="store_true",
        help="Force pure-Python backend (disable Rust extension)",
    )
    parser.add_argument(
        "--only", choices=["throughput", "per_element", "memory", "unparse"],
        help="Run only one benchmark section",
    )
    args = parser.parse_args()

    backend_label = "python (forced)" if args.python_only else "rust (if available)"
    print()
    print(f"  xmltodict isolated benchmark suite")
    print(f"  Backend: {backend_label}")
    print(f"  Runs per measurement: {args.runs} (median reported)")
    print(f"  Python: {sys.version.split()[0]}")
    print(f"  Each measurement runs in a fresh subprocess")
    print()

    # Check fixtures exist
    if not os.path.exists(FIXTURES_DIR) or not os.listdir(FIXTURES_DIR):
        print("  Fixtures not found. Run: python benchmarks/generate_fixtures.py")
        sys.exit(1)

    t_results = pe_results = m_results = u_results = {}

    if not args.only or args.only == "throughput":
        t_results = bench_parse_throughput(args.runs, args.python_only)
    if not args.only or args.only == "per_element":
        pe_results = bench_per_element(args.runs, args.python_only)
    if not args.only or args.only == "memory":
        m_results = bench_memory(args.runs, args.python_only)
    if not args.only or args.only == "unparse":
        u_results = bench_unparse_throughput(args.runs, args.python_only)

    if args.save:
        os.makedirs(os.path.dirname(args.save) if os.path.dirname(args.save) else ".", exist_ok=True)
        payload = {
            "parse_throughput": t_results,
            "per_element_us": pe_results,
            "memory_ratio": m_results,
            "unparse_throughput": u_results,
        }
        with open(args.save, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\n  Results saved to {args.save}")

    print()


if __name__ == "__main__":
    main()
