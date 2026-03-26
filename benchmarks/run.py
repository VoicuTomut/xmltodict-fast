"""
xmltodict benchmark suite — 4 core metrics

Metrics measured:
  1. parse()   throughput      MB/s  — how fast raw XML is consumed
  2. per-element cost          µs/element — cost of each open+text+close cycle
  3. memory ratio              x input size — RAM used vs file size
  4. unparse() throughput      MB/s  — how fast dict is serialized back to XML

Usage:
    # First time only — generate fixture files:
    python benchmarks/generate_fixtures.py

    # Run all benchmarks:
    python benchmarks/run.py

    # Run a single metric:
    python benchmarks/run.py --only throughput
    python benchmarks/run.py --only per_element
    python benchmarks/run.py --only memory
    python benchmarks/run.py --only unparse

    # Save results to JSON for later comparison:
    python benchmarks/run.py --save benchmarks/results/baseline.json
"""

import argparse
import gc
import json
import os
import sys
import time
import tracemalloc

# Make sure we import the local xmltodict, not a system-installed one
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import xmltodict

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
RESULTS_DIR  = os.path.join(os.path.dirname(__file__), "results")

# How many times to repeat each timed operation (median is reported)
REPEATS = 5

# Targets — what a faster implementation should reach
TARGET_PARSE_MBS      = 400    # MB/s
TARGET_PE_US          = 0.5    # µs/element
TARGET_MEMORY_RATIO   = 3.0    # × input size
TARGET_UNPARSE_MBS    = 200    # MB/s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(filename):
    path = os.path.join(FIXTURES_DIR, filename)
    if not os.path.exists(path):
        print(f"  [MISSING] {path}")
        print("  Run:  python benchmarks/generate_fixtures.py")
        sys.exit(1)
    with open(path, "rb") as f:
        return f.read()


def _median(times):
    s = sorted(times)
    n = len(s)
    return s[n // 2]


def _time_fn(fn, repeats=REPEATS):
    """Return median wall-clock time in seconds over `repeats` runs."""
    times = []
    for _ in range(repeats):
        gc.collect()
        gc.disable()
        t0 = time.perf_counter()
        fn()
        t1 = time.perf_counter()
        gc.enable()
        times.append(t1 - t0)
    return _median(times)


def _separator(char="─", width=72):
    print(char * width)


def _header(title):
    _separator("═")
    print(f"  {title}")
    _separator("═")


def _pct_to_target(measured, target, higher_is_better=True):
    """Return how far measured is from target as a readable string."""
    if higher_is_better:
        pct = (measured / target) * 100
    else:
        pct = (target / measured) * 100
    return f"{pct:.0f}% of target"


def _row(label, value, unit="", note=""):
    note_str = f"  ← {note}" if note else ""
    print(f"  {label:<30}  {value:>10}  {unit:<14}{note_str}")


# ---------------------------------------------------------------------------
# Benchmark 1 — parse() throughput  (MB/s)
# ---------------------------------------------------------------------------

def bench_parse_throughput():
    _header("BENCHMARK 1 — parse() throughput  (MB/s)")
    print(f"  Higher = faster.  Target: {TARGET_PARSE_MBS} MB/s\n")

    fixtures = [
        ("small.xml",      "~1 KB   — single API response"),
        ("medium.xml",     "~500 KB — RSS feed / 2 000 items"),
        ("large.xml",      "~10 MB  — data export / 40 000 records"),
        ("wide.xml",       "~1 MB   — 10 000 flat siblings"),
        ("deep.xml",       "~30 KB  — 500 levels of nesting"),
        ("namespaced.xml", "~500 KB — SOAP-style namespaced"),
    ]

    results = {}
    for filename, description in fixtures:
        data = _load(filename)
        size_mb = len(data) / (1024 * 1024)

        elapsed = _time_fn(lambda d=data: xmltodict.parse(d))
        throughput = size_mb / elapsed

        note = _pct_to_target(throughput, TARGET_PARSE_MBS, higher_is_better=True)
        _row(
            f"{filename}  ({description[:20]})",
            f"{throughput:.1f}",
            "MB/s",
            note,
        )
        results[filename] = {"size_mb": round(size_mb, 3), "throughput_mbs": round(throughput, 2)}

    print()
    return results


# ---------------------------------------------------------------------------
# Benchmark 2 — per-element cost  (µs/element)
# ---------------------------------------------------------------------------

def bench_per_element():
    _header("BENCHMARK 2 — per-element cost  (µs/element)")
    print(f"  Lower = faster.  Target: {TARGET_PE_US} µs/element")
    print("  Tests how cost scales with N (should be a straight line)\n")

    def _make_flat_xml(n):
        parts = [b'<?xml version="1.0" encoding="utf-8"?>\n<root>\n']
        for i in range(n):
            parts.append(
                f'  <item id="{i}"><name>item{i}</name>'
                f'<value>{i * 3}</value></item>\n'.encode()
            )
        parts.append(b"</root>")
        return b"".join(parts)

    counts = [100, 1_000, 5_000, 10_000, 50_000]
    results = {}

    for n in counts:
        xml = _make_flat_xml(n)
        elapsed = _time_fn(lambda x=xml: xmltodict.parse(x))
        us_per_element = (elapsed * 1_000_000) / n

        note = _pct_to_target(us_per_element, TARGET_PE_US, higher_is_better=False)
        _row(f"N = {n:>6,} elements", f"{us_per_element:.2f}", "µs/element", note)
        results[n] = round(us_per_element, 4)

    print()
    return results


# ---------------------------------------------------------------------------
# Benchmark 3 — memory ratio  (x input file size)
# ---------------------------------------------------------------------------

def bench_memory():
    _header("BENCHMARK 3 — memory ratio  (× input file size)")
    print(f"  Lower = better.  Target: {TARGET_MEMORY_RATIO}× input size")
    print("  Streaming mode should stay near-constant regardless of file size\n")

    fixtures = [
        ("medium.xml", "~500 KB"),
        ("large.xml",  "~10 MB"),
        ("wide.xml",   "~1 MB"),
    ]

    results = {}

    # Non-streaming
    print("  Non-streaming (full document load):")
    for filename, size_label in fixtures:
        data = _load(filename)
        size_bytes = len(data)

        gc.collect()
        tracemalloc.start()
        xmltodict.parse(data)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        ratio = peak / size_bytes
        size_mb = size_bytes / (1024 * 1024)
        peak_mb = peak / (1024 * 1024)

        note = _pct_to_target(ratio, TARGET_MEMORY_RATIO, higher_is_better=False)
        _row(
            f"  {filename}  ({size_label})",
            f"{ratio:.1f}×",
            f"({peak_mb:.1f} MB peak)",
            note,
        )
        results[f"{filename}_non_streaming"] = {
            "size_mb": round(size_mb, 3),
            "peak_mb": round(peak_mb, 3),
            "ratio": round(ratio, 2),
        }

    print()

    # Streaming — overhead should stay constant regardless of file size.
    # tracemalloc only sees CPython-managed allocations that happen *during*
    # parse(); `data` was allocated before .start() and is never counted.
    # We just report the raw peak: if streaming is correct it stays roughly
    # the same across small, medium and large files.
    print("  Streaming (item_depth=2, no-op callback):")
    def _noop(path, item):
        return True

    stream_rows = []
    for filename, size_label in fixtures:
        data = _load(filename)
        size_bytes = len(data)

        gc.collect()
        tracemalloc.start()
        xmltodict.parse(data, item_depth=2, item_callback=_noop)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        size_mb = size_bytes / (1024 * 1024)
        overhead_kb = peak / 1024
        stream_rows.append((filename, size_label, size_mb, overhead_kb))
        results[f"{filename}_streaming"] = {
            "size_mb": round(size_mb, 3),
            "overhead_kb": round(overhead_kb, 1),
        }

    # A leak shows up as overhead scaling with file size.
    # Flag if largest file uses >5× the overhead of the smallest file.
    overheads = [r[3] for r in stream_rows]
    ratio = max(overheads) / max(min(overheads), 1)
    leak = ratio > 5

    for filename, size_label, size_mb, overhead_kb in stream_rows:
        note = "LEAK — overhead grows with file size" if leak else "constant ✓"
        _row(
            f"  {filename}  ({size_label})",
            f"{overhead_kb:.0f}",
            "KB overhead",
            note,
        )

    print()
    return results


# ---------------------------------------------------------------------------
# Benchmark 4 — unparse() throughput  (MB/s)
# ---------------------------------------------------------------------------

def bench_unparse_throughput():
    _header("BENCHMARK 4 — unparse() throughput  (MB/s)")
    print(f"  Higher = faster.  Target: {TARGET_UNPARSE_MBS} MB/s\n")

    fixtures = [
        ("medium.xml", "~500 KB"),
        ("large.xml",  "~10 MB"),
        ("wide.xml",   "~1 MB"),
    ]

    results = {}

    for filename, size_label in fixtures:
        data = _load(filename)

        # Parse once to get the dict, then benchmark only unparse
        parsed = xmltodict.parse(data)
        output_xml = xmltodict.unparse(parsed)
        output_size_mb = len(output_xml.encode()) / (1024 * 1024)

        elapsed = _time_fn(lambda p=parsed: xmltodict.unparse(p))
        throughput = output_size_mb / elapsed

        note = _pct_to_target(throughput, TARGET_UNPARSE_MBS, higher_is_better=True)
        _row(
            f"{filename}  ({size_label})",
            f"{throughput:.1f}",
            "MB/s",
            note,
        )
        results[filename] = {
            "output_mb": round(output_size_mb, 3),
            "throughput_mbs": round(throughput, 2),
        }

    print()
    return results


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def print_summary(t_results, pe_results, m_results, u_results):
    _header("SUMMARY — Measured vs Target")

    print(f"  {'Metric':<25} {'Measured':<15} {'Target':<18} {'% of target':<15} {'Customer impact'}")
    _separator()

    tp = t_results.get("large.xml", {}).get("throughput_mbs", 0)
    tp_pct = f"{(tp / TARGET_PARSE_MBS * 100):.0f}%"
    print(f"  {'parse throughput':<25} {f'{tp:.1f} MB/s':<15} {f'{TARGET_PARSE_MBS} MB/s':<18} {tp_pct:<15} Lambda cost, latency")

    pe = pe_results.get(10_000, 0)
    pe_pct = f"{(TARGET_PE_US / pe * 100):.0f}%" if pe else "—"
    print(f"  {'per-element cost':<25} {f'{pe:.2f} µs/elem':<15} {f'{TARGET_PE_US} µs/elem':<18} {pe_pct:<15} Batch pipeline speed")

    mr = m_results.get("large.xml_non_streaming", {}).get("ratio", 0)
    mr_pct = f"{(TARGET_MEMORY_RATIO / mr * 100):.0f}%" if mr else "—"
    print(f"  {'memory ratio':<25} {f'{mr:.1f}× input':<15} {f'{TARGET_MEMORY_RATIO}× input':<18} {mr_pct:<15} Lambda tier selection")

    ut = u_results.get("large.xml", {}).get("throughput_mbs", 0)
    ut_pct = f"{(ut / TARGET_UNPARSE_MBS * 100):.0f}%"
    print(f"  {'unparse throughput':<25} {f'{ut:.1f} MB/s':<15} {f'{TARGET_UNPARSE_MBS} MB/s':<18} {ut_pct:<15} XML generation speed")

    _separator()
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="xmltodict benchmark suite")
    parser.add_argument(
        "--only",
        choices=["throughput", "per_element", "memory", "unparse"],
        help="Run only one benchmark",
    )
    parser.add_argument(
        "--save",
        metavar="FILE",
        help="Save results to a JSON file (e.g. benchmarks/results/baseline.json)",
    )
    parser.add_argument(
        "--python-only",
        action="store_true",
        help="Force the pure-Python path (disable Rust extension) for baseline measurement",
    )
    args = parser.parse_args()

    if args.python_only:
        xmltodict._RUST_AVAILABLE = False
        xmltodict._BACKEND = "python"

    print()
    print("  xmltodict benchmark suite")
    print(f"  xmltodict version: {getattr(xmltodict, '__version__', 'unknown')}")
    print(f"  Python: {sys.version.split()[0]}")
    print(f"  Backend: {'python (forced)' if getattr(args, 'python_only', False) else xmltodict._BACKEND}")
    print(f"  Repeats per measurement: {REPEATS} (median reported)")
    print()

    t_results = pe_results = m_results = u_results = {}

    if not args.only or args.only == "throughput":
        t_results = bench_parse_throughput()
    if not args.only or args.only == "per_element":
        pe_results = bench_per_element()
    if not args.only or args.only == "memory":
        m_results = bench_memory()
    if not args.only or args.only == "unparse":
        u_results = bench_unparse_throughput()

    if not args.only:
        print_summary(t_results, pe_results, m_results, u_results)

    if args.save:
        os.makedirs(os.path.dirname(args.save) if os.path.dirname(args.save) else ".", exist_ok=True)
        payload = {
            "parse_throughput":   t_results,
            "per_element_us":     pe_results,
            "memory_ratio":       m_results,
            "unparse_throughput": u_results,
        }
        with open(args.save, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"  Results saved to {args.save}")
        print()


if __name__ == "__main__":
    main()
