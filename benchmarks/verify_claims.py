"""
xmltodict-fast — claims verification benchmark

Runs both backends (Rust and pure Python) head-to-head and verifies
every public claim made in the README and release notes.

Usage:
    python benchmarks/verify_claims.py

Requirements:
    python benchmarks/generate_fixtures.py   # run once first
"""

import gc
import os
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import xmltodict

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
REPEATS = 7
W = 72

# ── helpers ───────────────────────────────────────────────────────────────────

def _load(name):
    path = os.path.join(FIXTURES_DIR, name)
    if not os.path.exists(path):
        print(f"  [MISSING] {path}")
        print("  Run: python benchmarks/generate_fixtures.py")
        sys.exit(1)
    return open(path, "rb").read()

def _median(fn, repeats=REPEATS):
    times = []
    for _ in range(repeats):
        gc.collect(); gc.disable()
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
        gc.enable()
    return sorted(times)[repeats // 2]

def _peak_kb(fn):
    gc.collect()
    tracemalloc.start()
    fn()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / 1024

def _sep(c="─"): print(c * W)
def _header(t): _sep("═"); print(f"  {t}"); _sep("═")

def _row(label, py_val, rs_val, unit, speedup=None, claim=None, higher_better=True):
    if speedup is None:
        speedup = (rs_val / py_val) if higher_better else (py_val / rs_val)
    ok = "✅" if (speedup >= 1.0) else "❌"
    claim_str = f"  (claimed {claim})" if claim else ""
    print(f"  {label:<28} Python: {py_val:>8.1f} {unit}   Rust: {rs_val:>8.1f} {unit}   {speedup:.1f}×{claim_str}  {ok}")

# ── backends ──────────────────────────────────────────────────────────────────

def with_rust(fn):
    xmltodict._RUST_AVAILABLE = True
    xmltodict._BACKEND = "rust"
    return fn()

def with_python(fn):
    xmltodict._RUST_AVAILABLE = False
    xmltodict._BACKEND = "python"
    result = fn()
    xmltodict._RUST_AVAILABLE = True
    xmltodict._BACKEND = "rust"
    return result

def throughput(data, **kwargs):
    mb = len(data) / (1024 * 1024)
    def _py(): with_python(lambda: xmltodict.parse(data, **kwargs))
    def _rs(): with_rust(lambda: xmltodict.parse(data, **kwargs))
    return mb / _median(_py), mb / _median(_rs)

def unparse_throughput(data):
    parsed = with_rust(lambda: xmltodict.parse(data))
    out_mb = len(with_rust(lambda: xmltodict.unparse(parsed)).encode()) / (1024 * 1024)
    def _py(): with_python(lambda: xmltodict.unparse(parsed))
    def _rs(): with_rust(lambda: xmltodict.unparse(parsed))
    return out_mb / _median(_py), out_mb / _median(_rs)

def memory_ratio(data):
    mb = len(data) / (1024 * 1024)
    py_kb = _peak_kb(lambda: with_python(lambda: xmltodict.parse(data)))
    rs_kb = _peak_kb(lambda: with_rust(lambda: xmltodict.parse(data)))
    return py_kb / 1024 / mb, rs_kb / 1024 / mb   # ratio = peak_mb / file_mb

def streaming_overhead_kb(data):
    def _noop(path, item): return True
    py_kb = _peak_kb(lambda: with_python(lambda: xmltodict.parse(data, item_depth=2, item_callback=_noop)))
    rs_kb = _peak_kb(lambda: with_rust(lambda: xmltodict.parse(data, item_depth=2, item_callback=_noop)))
    return py_kb, rs_kb

def per_element_us(n):
    parts = [b'<?xml version="1.0"?>\n<root>\n']
    for i in range(n):
        parts.append(f'  <item id="{i}"><v>{i}</v></item>\n'.encode())
    parts.append(b"</root>")
    data = b"".join(parts)
    def _py(): with_python(lambda: xmltodict.parse(data))
    def _rs(): with_rust(lambda: xmltodict.parse(data))
    return (_median(_py) * 1e6) / n, (_median(_rs) * 1e6) / n

# ── sections ──────────────────────────────────────────────────────────────────

def section_parse():
    _header("1 · parse() throughput  (MB/s, higher = faster)")
    fixtures = [
        ("small.xml",      "~1 KB"),
        ("medium.xml",     "~600 KB"),
        ("large.xml",      "~7 MB"),
        ("wide.xml",       "~800 KB  flat"),
        ("namespaced.xml", "~300 KB  namespaced"),
    ]
    results = {}
    for name, label in fixtures:
        data = _load(name)
        py_mbs, rs_mbs = throughput(data)
        _row(f"{name} ({label})", py_mbs, rs_mbs, "MB/s", claim="2–3×")
        results[name] = (py_mbs, rs_mbs)
    print()
    return results

def section_per_element():
    _header("2 · per-element cost  (µs/element, lower = faster)")
    counts = [100, 1_000, 10_000, 50_000]
    results = {}
    for n in counts:
        py_us, rs_us = per_element_us(n)
        speedup = py_us / rs_us
        ok = "✅" if speedup >= 2.0 else "❌"
        print(f"  N={n:>6,}   Python: {py_us:>6.2f} µs/elem   Rust: {rs_us:>6.2f} µs/elem   {speedup:.1f}×  {ok}")
        results[n] = (py_us, rs_us)
    print()
    return results

def section_memory():
    _header("3 · memory ratio  (× file size, lower = better)")
    fixtures = [("medium.xml", "~600 KB"), ("large.xml", "~7 MB"), ("wide.xml", "~800 KB")]
    results = {}
    for name, label in fixtures:
        data = _load(name)
        py_r, rs_r = memory_ratio(data)
        improvement = py_r / rs_r
        ok = "✅" if improvement >= 1.3 else "❌"
        print(f"  {name} ({label:<12})  Python: {py_r:>5.1f}×   Rust: {rs_r:>5.1f}×   {improvement:.1f}× less  {ok}")
        results[name] = (py_r, rs_r)
    print()
    return results

def section_streaming():
    _header("4 · streaming memory overhead  (KB, lower = better)")
    print("  Claim: Rust stays ~2 KB constant regardless of file size\n")
    fixtures = [("medium.xml", "~600 KB"), ("large.xml", "~7 MB"), ("wide.xml", "~800 KB")]
    rs_overheads = []
    results = {}
    for name, label in fixtures:
        data = _load(name)
        py_kb, rs_kb = streaming_overhead_kb(data)
        rs_overheads.append(rs_kb)
        ok = "✅" if rs_kb < 10 else "❌"
        print(f"  {name} ({label:<12})  Python: {py_kb:>7.0f} KB   Rust: {rs_kb:>5.1f} KB  {ok}")
        results[name] = (py_kb, rs_kb)
    # check constant — largest should not be >5× smallest
    ratio = max(rs_overheads) / max(min(rs_overheads), 0.1)
    constant_ok = "✅ constant" if ratio < 5 else "❌ grows with file size"
    print(f"\n  Rust overhead scaling: {ratio:.1f}× across fixtures → {constant_ok}")
    print()
    return results

def section_unparse():
    _header("5 · unparse() throughput  (MB/s, higher = faster)")
    fixtures = [("medium.xml", "~600 KB"), ("large.xml", "~7 MB"), ("wide.xml", "~800 KB")]
    results = {}
    for name, label in fixtures:
        data = _load(name)
        py_mbs, rs_mbs = unparse_throughput(data)
        _row(f"{name} ({label})", py_mbs, rs_mbs, "MB/s", claim="5–8×")
        results[name] = (py_mbs, rs_mbs)
    print()
    return results

def section_summary(parse_r, pe_r, mem_r, stream_r, unparse_r):
    _header("CLAIMS VERIFICATION SUMMARY")

    def check(label, condition, detail):
        icon = "✅  PASS" if condition else "❌  FAIL"
        print(f"  {icon}  {label}")
        print(f"         {detail}")

    # parse: at least 2× on large.xml
    py_p, rs_p = parse_r["large.xml"]
    check(
        "parse() at least 2× faster",
        rs_p / py_p >= 2.0,
        f"large.xml: {py_p:.1f} → {rs_p:.1f} MB/s  ({rs_p/py_p:.1f}×)",
    )

    # per-element: at least 2× at 10k
    py_pe, rs_pe = pe_r[10_000]
    check(
        "per-element cost at least 2× lower",
        py_pe / rs_pe >= 2.0,
        f"10k elements: {py_pe:.2f} → {rs_pe:.2f} µs/elem  ({py_pe/rs_pe:.1f}×)",
    )

    # memory: Rust uses less memory on large.xml
    py_m, rs_m = mem_r["large.xml"]
    check(
        "Rust uses less memory than Python",
        rs_m < py_m,
        f"large.xml: Python {py_m:.1f}× → Rust {rs_m:.1f}× of file size",
    )

    # streaming: Rust overhead < 10 KB on large.xml
    py_s, rs_s = stream_r["large.xml"]
    check(
        "streaming overhead < 10 KB (Rust)",
        rs_s < 10,
        f"large.xml: Python {py_s:.0f} KB → Rust {rs_s:.1f} KB",
    )

    # unparse: at least 5× on large.xml
    py_u, rs_u = unparse_r["large.xml"]
    check(
        "unparse() at least 5× faster",
        rs_u / py_u >= 5.0,
        f"large.xml: {py_u:.1f} → {rs_u:.1f} MB/s  ({rs_u/py_u:.1f}×)",
    )

    _sep()
    print()

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print()
    print("  xmltodict-fast — claims verification")
    print(f"  Backend: {xmltodict._BACKEND}  |  Python {sys.version.split()[0]}")
    print(f"  Repeats per measurement: {REPEATS} (median)")
    print()

    parse_r   = section_parse()
    pe_r      = section_per_element()
    mem_r     = section_memory()
    stream_r  = section_streaming()
    unparse_r = section_unparse()
    section_summary(parse_r, pe_r, mem_r, stream_r, unparse_r)

if __name__ == "__main__":
    main()
