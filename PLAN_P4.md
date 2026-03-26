# Phase 4 Plan — PyString Key Interning in Rust parse()

## Status: DONE

## Problem

**Current state (after P3):**

| Fixture   | Memory ratio | Target | Gap  |
|-----------|-------------|--------|------|
| medium.xml | 3.1×        | 3.0×   | ✅ nearly there |
| large.xml  | 3.8×        | 3.0×   | ❌ 27% over     |
| wide.xml   | 6.7×        | 3.0×   | ❌ 2.2× over    |

The wide.xml fixture (~1 MB, 10 000 flat `<item>` siblings) is the primary target.
Each item has two attributes (`@id`, `@type`) and two child elements (`<name>`, `<value>`).

**Root cause: repeated PyString allocations for identical keys.**

The Rust parser currently creates a new `PyString` object for every dict key on every
element, even when the key string is identical across thousands of elements.

For wide.xml (10 000 items × 5 repeating keys):

| Key string | Occurrences | Per-object size | Wasted bytes |
|-----------|-------------|-----------------|--------------|
| `"item"`  | 10 000      | ~56             | 559 944      |
| `"@id"`   | 10 000      | ~53             | 529 947      |
| `"@type"` | 10 000      | ~55             | 549 945      |
| `"name"`  | 10 000      | ~53             | 529 947      |
| `"value"` | 10 000      | ~54             | 539 946      |

Total avoidable allocation: **~2.71 MB**

Current peak: 5.4 MB → after interning: ~2.7 MB → ratio drops from **6.7× to ~2.7×** — below target.

Bonus: the `"@type"` value `"node"` is repeated 10 000 times (540 KB). Adding value
interning for short, repeated strings extends the gain to large.xml as well.

---

## Solution

**Session-level key cache in `src/parse.rs`.**

Add a `HashMap<String, Py<PyString>>` to the parse session. Before inserting any
string as a PyDict key, look it up in the cache. On a cache miss, create one PyString,
store it, and return a clone of the owned reference. On a cache hit, return a clone of
the cached reference — no new allocation.

This applies to:
1. **Element names** used as dict keys when attaching a child to its parent (`push_data`)
2. **Attribute key strings** built as `format!("{attr_prefix}{key}")` in `build_attrs_dict`

Value interning is a secondary optimization (smaller gain, less predictable) and is
**out of scope** for P4.

---

## Implementation spec

### New data structure

```rust
/// Per-parse-call cache of Python string objects, keyed by their Rust string content.
/// Reduces allocation for documents with many elements sharing the same key names.
type KeyCache = HashMap<String, Py<PyString>>;

/// Look up or create a cached PyString for `key`.
fn intern_key(py: Python<'_>, cache: &mut KeyCache, key: &str) -> Py<PyString> {
    if let Some(cached) = cache.get(key) {
        cached.clone_ref(py)
    } else {
        let py_str = PyString::new_bound(py, key).unbind();
        cache.insert(key.to_string(), py_str.clone_ref(py));
        py_str
    }
}
```

### Changes to `push_data`

**Before:**
```rust
fn push_data(
    py: Python<'_>,
    item: Option<Py<PyDict>>,
    key: &str,
    data: Option<PyObject>,
    force_list: &Option<Vec<String>>,
) -> PyResult<Py<PyDict>> {
    // ...
    dict.set_item(key, ...)?;
    // ...
}
```

**After:**
```rust
fn push_data(
    py: Python<'_>,
    item: Option<Py<PyDict>>,
    key: &str,
    data: Option<PyObject>,
    force_list: &Option<Vec<String>>,
    key_cache: &mut KeyCache,            // ← new parameter
) -> PyResult<Py<PyDict>> {
    // ...
    let py_key = intern_key(py, key_cache, key);
    dict.set_item(py_key.bind(py), ...)?;
    // ...
}
```

All three `dict.set_item(key, ...)` calls in `push_data` use `py_key.bind(py)`.
The `dict.get_item(key)?` lookup can stay as `&str` — PyO3 handles that comparison
against existing PyString keys via `__eq__`, which is fine for correctness and is not
on the hot path (we only do it once per key occurrence).

### Changes to `build_attrs_dict`

**Before:**
```rust
fn build_attrs_dict(
    py: Python<'_>,
    attributes: ...,
    attr_prefix: &str,
    reader: &Reader<&[u8]>,
) -> PyResult<Option<Py<PyDict>>> {
    // ...
    let full_key = format!("{attr_prefix}{key_str}");
    d.bind(py).set_item(&full_key, val.as_ref())?;
    // ...
}
```

**After:**
```rust
fn build_attrs_dict(
    py: Python<'_>,
    attributes: ...,
    attr_prefix: &str,
    reader: &Reader<&[u8]>,
    key_cache: &mut KeyCache,            // ← new parameter
) -> PyResult<Option<Py<PyDict>>> {
    // ...
    let full_key = format!("{attr_prefix}{key_str}");
    let py_key = intern_key(py, key_cache, &full_key);
    d.bind(py).set_item(py_key.bind(py), val.as_ref())?;
    // ...
}
```

### Thread the cache through `parse()`

In `parse()`:
```rust
let mut key_cache: KeyCache = HashMap::new();

// Pass to build_attrs_dict
let item = build_attrs_dict(py, e.attributes(), attr_prefix, &reader, &mut key_cache)?;

// Pass to push_data
let new_item = push_data(py, parent_item, &name, value, &force_list, &mut key_cache)?;
```

The cache is local to each `parse()` call. No global state, no thread-safety concerns.

---

## Files changed

| File | Change |
|------|--------|
| `src/parse.rs` | Add `KeyCache` type alias + `intern_key()` helper; add `key_cache: &mut KeyCache` parameter to `push_data` and `build_attrs_dict`; thread cache through the event loop in `parse()` |

**No other files change.** Routing in `__init__.py`, unparse.rs, tests, benchmarks — all unchanged.

---

## Verifying the fix

After implementing, run:

```bash
python -m pytest tests/ --tb=short          # must still be 217/217
python benchmarks/run.py --only memory      # wide.xml ratio should drop to ~3× or below
python benchmarks/run.py --save benchmarks/results/after_p4.json
python benchmarks/compare.py benchmarks/results/after_p3.json benchmarks/results/after_p4.json
```

Expected comparison output:
- `wide.xml` memory: **6.7× → ≤ 3.0×** (winner: after_p4 by ~55%+)
- `medium.xml` memory: **3.1× → ≤ 3.0×** (small improvement from attr key reuse in RSS feed)
- `large.xml` memory: **3.8× → ~3.5×** (moderate improvement)
- Throughput (parse/unparse): within noise — no regression expected

---

## Why not value interning?

Values that repeat (e.g. `"node"` appearing 10 000 times) could also be interned with
the same mechanism. This was considered and deferred because:

1. **Key gains are larger and more predictable** — key strings are short and repeat exactly.
   Value strings vary in length and frequency; many are unique (IDs, timestamps, names).
2. **Risk of unbounded cache growth** — documents with 50 000 unique values would make the
   cache larger than just allocating the strings. A size-capped LRU cache would be needed.
3. **Keys alone close the gap** — the 2.71 MB savings from keys is enough to reach the 3×
   target. Value interning is a diminishing-return optimization.

Value interning can be added in a follow-up as a separate, benchmarked change if large.xml
still falls short after P4.

---

## Why not a global / module-level cache?

A module-level cache would need a Mutex for thread safety. Under concurrent parse() calls
from multiple threads (common in web servers), lock contention would cancel out the memory
savings in throughput. A per-call HashMap avoids all synchronization overhead.

---

## Edge cases

- **`attr_prefix = ""`**: `format!("{attr_prefix}{key_str}")` = `key_str` — no prefix. The
  cache still works correctly; the lookup key is just the bare attribute name.
- **Very long key strings**: interned like any other. The HashMap stores one copy regardless
  of length. No special handling needed.
- **Single-element documents** (`small.xml`): cache has 0 or 1 hits — zero overhead from
  the HashMap lookup because HashMap::get on a small map is O(1) with near-zero constant.

---

## Commit message template

```
perf(P4): intern repeated PyString keys in Rust parse()

Add a per-call KeyCache (HashMap<String, Py<PyString>>) to push_data and
build_attrs_dict. Element names and attribute key strings are now allocated
once per unique value per parse() call rather than once per occurrence.

Reduces wide.xml memory ratio from 6.7× to ~2.7× on a 10 000-element
document where each element repeats the same 5 key strings.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```
