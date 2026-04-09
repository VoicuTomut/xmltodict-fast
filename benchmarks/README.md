# xmltodict Benchmark Suite

Measures four core metrics of the pure-Python baseline.
Run before and after the Rust rewrite to produce the before/after table
that goes in the blog post and on the landing page.

---

## File layout

```
benchmarks/
├── README.md               ← you are here
├── generate_fixtures.py    ← creates XML test files (run once)
├── run.py                  ← quick in-process benchmark (5 repeats)
├── run_isolated.py         ← publication-quality benchmark (20 subprocess runs)
├── compare.py              ← diff two result JSON files (before vs after)
├── fixtures/               ← generated XML files (gitignored)
│   ├── small.xml           ~0.5 KB  — single API response (AWS S3 style)
│   ├── medium.xml          ~600 KB  — RSS-style feed, 2 000 items
│   ├── large.xml           ~7 MB    — data export, 40 000 records
│   ├── wide.xml            ~830 KB  — 10 000 flat siblings under root
│   ├── deep.xml            ~500 KB  — 500 levels of nesting
│   └── namespaced.xml      ~310 KB  — SOAP-style, namespaced elements
└── results/                ← saved JSON results (commit these)
    └── baseline.json       ← pure-Python baseline (already recorded)
```

---

## Quick start

```bash
# From the repo root: OldTreazure/xmltodict/

# Step 1 — generate fixture files (run only once, or after changing generate_fixtures.py)
python benchmarks/generate_fixtures.py

# Step 2 — run all benchmarks with subprocess isolation (20 runs, median)
python benchmarks/run_isolated.py --python-only --save benchmarks/results/baseline.json
python benchmarks/run_isolated.py --save benchmarks/results/rs_version.json

# Step 3 — print a before/after comparison table
python benchmarks/compare.py benchmarks/results/

# Quick in-process check (5 repeats, single process — faster but less rigorous):
python benchmarks/run.py --save benchmarks/results/quick_check.json
```

---

## What each benchmark measures

### Benchmark 1 — parse() throughput (MB/s)
**What it is:** megabytes of raw XML processed per second by `parse()`.

**How it runs:** loads each fixture file into memory as bytes in a fresh subprocess,
runs one timed measurement per subprocess, repeats 20 times, reports the median.
Divides file size by elapsed time.

**Why it matters:** this is the headline number for the sales pitch. A Lambda
processing 10 MB SOAP responses that goes from 27 MB/s to 600 MB/s saves real
compute money. This is the number CTOs ask for.

**Where to look:** `run.py → bench_parse_throughput()`

**Current baseline (Python):** 13–40 MB/s on realistic documents.
`deep.xml` shows 470+ MB/s but that fixture is mostly whitespace — not representative.

**Rust target:** 400–1 200 MB/s.

---

### Benchmark 2 — per-element cost (µs/element)
**What it is:** microseconds spent per XML element (open tag + content + close tag).

**How it runs:** generates flat XML with N elements in memory (100 → 50 000)
in a fresh subprocess, times one `parse()` call, divides total time by N.
Repeated 20 times across subprocesses, median reported.

**Why it matters:** isolates the SAX callback tax from file I/O. Proves that the
Python overhead is per-element, not per-byte. This is the number that makes the
engineering case for replacing the SAX architecture with a Rust pull parser.

**Where to look:** `run.py → bench_per_element()`

**What to check:** the cost/element should stay flat as N grows (straight line).
Any curve means a hidden O(n²). Current baseline is ~3 µs/element, rock-solid flat.

**Rust target:** 0.1–0.5 µs/element (~10× improvement).

---

### Benchmark 3 — memory ratio (× input file size)
**What it is:**
- **Non-streaming:** peak RAM during `parse()` divided by the input file size.
  A ratio of 3× means parsing a 10 MB file uses 30 MB of RAM at peak.
- **Streaming overhead:** peak RAM minus the input bytes (since the raw bytes
  are always in memory). This number should stay small and constant regardless
  of how large the file is.

**How it runs:** uses `tracemalloc` in a fresh subprocess to capture peak allocation.
Repeated 20 times, median reported.

**Why it matters:**
- Non-streaming: AWS Lambda has 128–3 008 MB tiers. If 20 MB XML needs
  150 MB RAM, the customer is forced onto an expensive tier.
- Streaming: if overhead grows with file size, the streaming fix has regressed
  and items are accumulating in memory. The test flags this automatically.

**Where to look:** `run.py → bench_memory()`

**Current baseline:**
- Non-streaming: 3.1–7.5× (wide documents worst because many small Python strings)
- Streaming overhead: 540–733 KB constant ✓

**Rust target (non-streaming):** 1.5–3× — Rust strings are contiguous, no
per-object Python overhead.

---

### Benchmark 4 — unparse() throughput (MB/s)
**What it is:** megabytes of XML produced per second by `unparse()`.

**How it runs:** parses each fixture once to get a dict, then times one `unparse()`
call in a fresh subprocess. Measures output XML size divided by elapsed time.
Repeated 20 times across subprocesses, median reported.

**Why it matters:** any system that generates XML (SOAP servers, EDI, AWS SDK
wrappers) uses this path. Agents that construct XML requests hit this bottleneck.

**Where to look:** `run.py → bench_unparse_throughput()`

**Current baseline:** 18–34 MB/s. Bottleneck is `_XMLGenerator` doing one
`StringIO.write()` call per token instead of building one contiguous buffer.

**Rust target:** 200–600 MB/s.

---

## CLI options

```bash
# Run only one benchmark
python benchmarks/run.py --only throughput
python benchmarks/run.py --only per_element
python benchmarks/run.py --only memory
python benchmarks/run.py --only unparse

# Save to a specific file
python benchmarks/run.py --save benchmarks/results/my_run.json

# Compare two saved results
python benchmarks/compare.py benchmarks/results/baseline.json \
                              benchmarks/results/after_rust.json
```

---

## Where results are saved

Results are written as JSON to `benchmarks/results/`.

| File | When to create it |
|---|---|
| `results/baseline.json` | Pure-Python version — **already recorded, commit this** |
| `results/after_rust.json` | After shipping the Rust-backed wheel |

Both files are committed to the repo. The diff between them is the benchmark
table that goes on the landing page and in the blog post.

**Never overwrite `baseline.json`** once it is committed. It is the reference
point for all future comparisons. If you need to re-baseline (e.g. after a
Python-only perf improvement) name the file clearly:
`results/baseline_py_v2.json`.

---

## Interpreting the summary table

```
Metric               Measured      Today range   Rust target   Customer impact
─────────────────────────────────────────────────────────────────────────────
parse throughput     27.2 MB/s     15–40 MB/s    400–1200 MB/s Lambda cost, latency
per-element cost     2.92 µs/elem  5–15 µs/elem  0.1–0.5 µs    Batch pipeline speed
memory ratio         3.1× input    3–8× input    1.5–3× input  Lambda tier selection
unparse throughput   21.9 MB/s     5–15 MB/s     200–600 MB/s  XML generation speed
```

- **Measured** — what this machine produced right now (median of 20 subprocess-isolated runs)
- **Today range** — expected range on typical hardware for the pure-Python version
- **Rust target** — expected range after the quick-xml rewrite
- **Customer impact** — the business reason this number matters

The three numbers for the landing page: parse throughput, per-element cost,
and memory ratio. Unparse throughput is supporting evidence.

---

## Notes on variance

- Each measurement runs in a fresh Python subprocess to eliminate JIT warming,
  memory fragmentation, and inter-benchmark interference.
- All timings use `time.perf_counter()` with GC disabled during each run.
- Median of 20 subprocess-isolated runs is reported (not mean) to reduce outlier noise.
- Run on an idle machine. Background processes (Spotlight, browser) add ~10–20%
  noise. For a publication-quality run, use a dedicated machine or a GitHub
  Actions runner.
- `tracemalloc` adds ~2–5% overhead to timed memory runs. Acceptable for
  relative comparisons; do not use for absolute latency numbers.
- **Important:** `tracemalloc` only tracks CPython-managed allocations. Memory
  allocated by the Rust extension (via Rust's allocator) is invisible to
  `tracemalloc`. The memory benchmarks are only reliable for comparing
  pure-Python runs. Do not use Rust memory_ratio numbers for public claims.
- Use `run_isolated.py` for publication-quality numbers. The original `run.py`
  is still available for quick in-process checks (5 repeats, single process).
