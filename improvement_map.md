# Improvement Map — xmltodict Rust Integration

## Can Rust be added without breaking existing users?

**Yes. Zero breaking changes to the public API.**

The strategy is called a *graceful fallback*:
1. Ship a Rust extension (`.so` / `.pyd` wheel) with the fast implementation.
2. Keep the existing pure-Python code as a fallback.
3. The package's `__init__.py` tries to load the Rust extension first.
   If it fails (PyPy, unsupported platform, older OS), it silently falls back
   to pure Python.
4. Users never change their code. `import xmltodict` and both `parse()` /
   `unparse()` work exactly the same — just faster.

**One critical constraint from the current project:**
`pyproject.toml` lists PyPy as a supported platform. PyPy cannot load CPython
C extensions. The pure-Python fallback is therefore not optional — it is
required to maintain the existing compatibility promise.

---

## Priority order

Sorted by: **benchmark gain × implementation risk × user impact**.

---

### P1 — Maturin scaffolding
**Benchmark gain:** prerequisite for all Rust work
**Risk:** low — no logic changes, just build plumbing
**Blocks:** everything below

This is the only build-system change in the whole plan. Do this first, alone,
and verify that `maturin develop --release` installs correctly and all 217
tests still pass before writing a single line of Rust.

Note on installation:
- **You (dev):** `maturin develop --release` — compiles Rust + installs into your env. Requires Rust toolchain installed once via rustup.
- **End users:** `pip install agentxml` — downloads a pre-built wheel from PyPI. No Rust, no compiler, just works.
- **PyPy / unsupported platforms:** pip falls back to the sdist (pure Python). Same API, pure-Python speed.

#### Files to change

**`pyproject.toml`** — swap build backend from setuptools to maturin:
```toml
[build-system]
requires = ["maturin>=1.4,<2"]
build-backend = "maturin"

[tool.maturin]
python-source = "python"          # pure-Python package lives in python/
features = ["pyo3/extension-module"]
```

**`Cargo.toml`** — new file at repo root:
```toml
[package]
name = "xmltodict-rs"
version = "0.1.0"
edition = "2021"

[lib]
name = "_xmltodict_rs"            # the .so name Python will import
crate-type = ["cdylib"]

[dependencies]
pyo3   = { version = "0.21", features = ["extension-module"] }
quick-xml = { version = "0.36", features = ["encoding"] }
```

#### New directory layout after this step:
```
xmltodict/                    ← repo root
├── Cargo.toml                ← NEW
├── src/
│   └── lib.rs                ← NEW (empty stub for now)
├── python/
│   └── xmltodict/
│       ├── __init__.py       ← NEW (fallback wrapper)
│       └── _pure.py          ← RENAMED from xmltodict.py
├── tests/                    ← unchanged
├── pyproject.toml            ← CHANGED
└── ...
```

**`python/xmltodict/__init__.py`** — the fallback wrapper:
```python
try:
    from ._xmltodict_rs import parse, unparse, ParsingInterrupted
    _BACKEND = "rust"
except ImportError:
    from ._pure import parse, unparse, ParsingInterrupted
    _BACKEND = "python"

__all__ = ["parse", "unparse", "ParsingInterrupted"]
```

**`tox.ini`** — add maturin develop step before running tests:
```ini
[testenv]
extras = test
commands_pre = maturin develop --release
commands     = pytest --cov=xmltodict
```

---

### P2 — Rust parse() with quick-xml
**Benchmark gain:** parse throughput 27 MB/s → 400+ MB/s (+1 400%)
**Per-element cost:** 3.0 µs → 0.1–0.5 µs (+600%)
**Risk:** medium — must match Python output exactly for all kwargs

This is the core rewrite. The Rust function receives the XML bytes, runs a
quick-xml pull-parser loop, and returns a Python dict directly via PyO3.

#### What changes

**`src/lib.rs`** — register the parse function:
```rust
use pyo3::prelude::*;

mod parse;
mod unparse;

#[pymodule]
fn _xmltodict_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse::parse, m)?)?;
    m.add_function(wrap_pyfunction!(unparse::unparse, m)?)?;
    m.add_class::<parse::ParsingInterrupted>()?;
    Ok(())
}
```

**`src/parse.rs`** — new file implementing `parse()`:
- Accept the same kwargs as the Python version
  (`xml_attribs`, `attr_prefix`, `cdata_key`, `force_cdata`, `force_list`,
   `postprocessor`, `dict_constructor`, `strip_whitespace`, `namespaces`,
   `process_namespaces`, `process_comments`, `item_depth`, `item_callback`,
   `cdata_separator`, `disable_entities`, `encoding`)
- Use `quick_xml::Reader` in pull-parser mode (no callbacks, no Python GIL
  held during XML tokenization)
- Build `PyDict` / `PyList` / `PyString` directly via PyO3
- Release the GIL (`py.allow_threads(|| ...)`) during the XML tokenization
  loop — pure Rust, no Python objects touched inside
- Re-acquire GIL only when inserting into PyDict

**Start with the simple kwargs only.** Ship `xml_attribs`, `attr_prefix`,
`cdata_key`, `strip_whitespace`, `force_cdata=bool`, `force_list=tuple`.
For the complex ones (`postprocessor`, `force_cdata=callable`,
`force_list=callable`, `item_callback`) route to the Python fallback
rather than trying to call Python callables from Rust on day one.

#### Routing logic in `__init__.py`:
```python
def parse(xml_input, **kwargs):
    _complex = ("postprocessor", "item_callback")
    if any(callable(kwargs.get(k)) for k in _complex):
        from ._pure import parse as _py_parse
        return _py_parse(xml_input, **kwargs)
    return _rs_parse(xml_input, **kwargs)
```

This means the fast path covers ~90% of real-world usage. The callback
paths stay on Python until you add Rust callback support later.

#### Tests — nothing changes
All 217 existing tests run against the Rust implementation automatically.
They are your correctness guard.

---

### P3 — Rust unparse() with string builder
**Benchmark gain:** unparse throughput 22 MB/s → 200+ MB/s (+900%)
**Risk:** low-medium — output is a string, easy to diff against Python output

The Python bottleneck is `_XMLGenerator` writing to a `StringIO` one token
at a time. Rust replaces this with a single pre-allocated `String` buffer.

#### What changes

**`src/unparse.rs`** — new file implementing `unparse()`:
- Walk the Python dict recursively via PyO3 (`dict.iter()`)
- Write directly into a Rust `String` with pre-allocated capacity
  (`String::with_capacity(estimated_size)`)
- Apply XML escaping inline (no SAX layer)
- Return the finished `String` as a Python `str`

#### Simple kwargs first, same routing strategy as parse:
```python
def unparse(input_dict, **kwargs):
    if kwargs.get("preprocessor") or kwargs.get("output"):
        from ._pure import unparse as _py_unparse
        return _py_unparse(input_dict, **kwargs)
    return _rs_unparse(input_dict, **kwargs)
```

---

### P4 — Memory optimization for wide documents
**Benchmark gain:** memory ratio 7.5× → <3× for wide documents (+150%)
**Risk:** low — pure Python change, no Rust required

The wide.xml fixture (10 000 flat siblings) uses 7.5× memory because each
Python string element in the dict carries ~60 bytes of object overhead
regardless of its content length. Rust's `String` type has no per-object
overhead — this improvement comes automatically once P2 is done for the
parse path.

For the pure-Python fallback, the gain is smaller but achievable by
switching `dict_constructor` to a `__slots__`-based structure for leaf
nodes. Do this only after P2 and P3 are shipped — the Rust path already
solves it, and this would only help PyPy users.

---

### P5 — Streaming in Rust
**Benchmark gain:** streaming overhead 700 KB → <50 KB
**Risk:** medium-high — requires crossing Python/Rust boundary per callback

The streaming mode (`item_depth > 0`, `item_callback`) fires a Python
callable on every item. Calling Python from Rust requires re-acquiring the
GIL on every callback — this limits the speedup compared to non-streaming.

Still worth doing because:
- The XML tokenization between callbacks is pure Rust
- Memory stays constant by design

Implementation approach: tokenize in Rust, build the item dict in Rust,
acquire GIL only to call `item_callback(path, item)`, release immediately.
This gives most of the throughput gain while staying correct.

**Do this after P2 and P3 are stable.** It is the most complex PyO3 work.

---

### P6 — Wheel building CI
**Benchmark gain:** none — this is distribution, not performance
**Risk:** low — standard maturin + GitHub Actions pattern
**Required before shipping**

#### New file: `.github/workflows/release.yml`

Build wheels for all platforms users run on:

| Platform | Why |
|---|---|
| `linux/x86_64` | Most servers, Docker, Lambda x86 |
| `linux/aarch64` | AWS Lambda arm64 (cheaper tier) |
| `macos/x86_64` | Intel Macs |
| `macos/arm64` | M1/M2/M3 Macs |
| `windows/x86_64` | Windows users |

Use `maturin publish` with the PyPI token from the `.env` file.

PyPy users automatically get the pure-Python wheel (sdist) — no Rust
compiler required on their side.

---

### P7 — Package rename and Fair Source license
**Benchmark gain:** none — commercial
**Risk:** low — just metadata changes

When ready to publish as `agentxml`:

**`pyproject.toml`:**
```toml
[project]
name = "agentxml"
```

**`python/xmltodict/__init__.py`** — add deprecation shim so existing
`import xmltodict` still works with a warning:
```python
# In a separate xmltodict_compat package or via import hook
import warnings
warnings.warn("xmltodict is now agentxml. ...", DeprecationWarning)
from agentxml import *
```

**License:** replace `LICENSE` with Fair Source license text.
Set `license = "FSL-1.1-MIT"` in `pyproject.toml`.

---

## Summary table

| Priority | Change | Files affected | Benchmark impact | Risk |
|---|---|---|---|---|
| P1 | Maturin scaffolding | `pyproject.toml`, `Cargo.toml`, `src/lib.rs`, `python/xmltodict/__init__.py`, `_pure.py` | prerequisite | low |
| P2 | Rust parse() | `src/parse.rs`, `__init__.py` | parse +1400%, per-elem +600% | medium |
| P3 | Rust unparse() | `src/unparse.rs`, `__init__.py` | unparse +900% | low-medium |
| P4 | Memory wide docs | pure Python only — no new files | memory 7.5× → 3× | low |
| P5 | Rust streaming | `src/parse.rs` extension | streaming overhead -95% | high |
| P6 | Wheel CI | `.github/workflows/release.yml` | distribution | low |
| P7 | Rename + license | `pyproject.toml`, `LICENSE` | commercial | low |

## What never changes regardless of priority

- `tests/test_xmltodict.py` — untouched, runs against Rust automatically
- `tests/test_dicttoxml.py` — untouched
- `tests/test_extended.py` — untouched
- Public kwargs signatures of `parse()` and `unparse()`
- `ParsingInterrupted` exception name and behaviour
- `benchmarks/results/baseline.json` — the reference stays pure Python
