# Phase 6 Plan — PyString Value Interning in Rust parse()

## Status: DONE

## Problem

**Current state (after P5):**

| Fixture    | Memory ratio | Target | Gap  |
|------------|-------------|--------|------|
| medium.xml | 2.2×        | 3.0×   | ✅   |
| large.xml  | 2.5×        | 3.0×   | ✅   |
| wide.xml   | 4.5×        | 3.0×   | ❌ 50% over |

wide.xml (~1 MB, 10 000 flat `<item>` siblings) is the last fixture above target.

**Root cause: repeated PyString allocations for identical attribute values.**

After P4 (key interning), all 5 key strings are shared.  The remaining cost is
the values.  wide.xml items have four value slots each:

| Value      | Occurrences | Per-object size | Saveable bytes |
|-----------|-------------|-----------------|----------------|
| `"node"`  | 10 000      | ~54             | ~539 946       |
| id / name / value | 10 000 each | varies | 0 (all unique) |

`"node"` alone accounts for **~540 KB** of avoidable allocation.

Current peak: 3.7 MB → after interning `"node"`: ~3.16 MB
Ratio: 3.16 / 1.05 MB ≈ **3.0×** — right at target.

---

## Solution

**Size-capped per-call value cache in `src/parse.rs`.**

Reuse the existing `KeyCache` type.  Add a second cache (`value_cache`) with:

1. **Length guard** — only intern values with `len ≤ VALUE_LEN_MAX` (32 bytes).
   Long values (IDs, timestamps, prose) are almost never repeated; interning
   them wastes cache slots and adds HashMap overhead.

2. **Capacity cap** — once the cache holds `VALUE_CACHE_MAX` (512) entries,
   new unique values are no longer inserted. Cache hits still work for already-
   cached values.  This bounds worst-case memory to ~512 × 56 bytes ≈ 28 KB.

`try_intern_value` differs from `intern_key` in that it returns
`Option<Py<PyString>>`: `None` when the value is too long or the cache is full
and the value is new.  The caller falls back to a fresh `PyString::new`.

---

## Implementation spec

### New constant and helper

```rust
const VALUE_LEN_MAX: usize = 32;
const VALUE_CACHE_MAX: usize = 512;

/// Like intern_key, but size-capped: only short values are cached, and no new
/// entries are added once the cache reaches VALUE_CACHE_MAX.
fn try_intern_value(py: Python<'_>, cache: &mut KeyCache, value: &str) -> Option<Py<PyString>> {
    if value.len() > VALUE_LEN_MAX {
        return None;
    }
    if let Some(cached) = cache.get(value) {
        return Some(cached.clone_ref(py));
    }
    if cache.len() >= VALUE_CACHE_MAX {
        return None; // cache full — don't evict, just skip
    }
    let py_str = PyString::new(py, value).unbind();
    cache.insert(value.to_string(), py_str.clone_ref(py));
    Some(py_str)
}
```

### Changes to `build_attrs_dict`

Add `value_cache: &mut KeyCache` parameter.  Replace the `set_item(val.as_ref())`
call with:

```rust
let val_py: PyObject = if let Some(s) = try_intern_value(py, value_cache, val.as_ref()) {
    s.into_bound(py).into()
} else {
    PyString::new(py, val.as_ref()).into()
};
d.bind(py).set_item(py_key.bind(py), val_py)?;
```

### Changes to `Event::End` text handling

Replace:

```rust
data.map(|s| PyString::new(py, &s).into())
```

With:

```rust
data.map(|s| {
    if let Some(py_str) = try_intern_value(py, &mut value_cache, &s) {
        py_str.into_bound(py).into()
    } else {
        PyString::new(py, &s).into()
    }
})
```

### Thread value_cache through `parse()`

```rust
let mut value_cache: KeyCache = HashMap::new();

// Pass to build_attrs_dict
build_attrs_dict(py, e.attributes(), attr_prefix, &reader, &mut key_cache, &mut value_cache)?

// Event::End text fallback — inline (see above)
```

---

## Files changed

| File | Change |
|------|--------|
| `src/parse.rs` | Add `VALUE_LEN_MAX`, `VALUE_CACHE_MAX`, `try_intern_value()`; add `value_cache` parameter to `build_attrs_dict`; thread through `parse()` event loop |

---

## Verifying the fix

```bash
python -m pytest tests/ --tb=short          # must still be 217/217
python benchmarks/run.py --save benchmarks/results/after_p6.json
python benchmarks/compare.py benchmarks/results/after_p5.json benchmarks/results/after_p6.json
```

Expected:
- `wide.xml` memory ratio: **4.5× → ~3.0×**
- `medium.xml`, `large.xml`: small improvement or unchanged
- Throughput / per-element: within noise

---

## Why size-capped and not LRU?

An LRU cache would evict cold entries to make room for hot ones, giving better
hit rates on documents with > VALUE_CACHE_MAX distinct short values.  But LRU
requires either a linked list + HashMap or a third-party crate.  The simpler
"insert until full, then only serve hits" strategy is correct for xmltodict:

- The pathological case (> 512 distinct short values in one document) is rare.
- In the typical case (a handful of repeated enum-like values), the cache fills
  slowly and serves many hits.
- If the cache does fill, unique values after that point are just allocated
  normally — no correctness issue, just missed savings.

---

## Commit message template

```
perf(P6): intern repeated PyString values in Rust parse()

Add a size-capped per-call value cache (max 512 entries, values ≤ 32 bytes).
Attribute values and text-node strings that repeat across elements are now
allocated once per unique value per parse() call.

Reduces wide.xml memory ratio from 4.5× to ~3.0× on a 10 000-element
document where "@type"="node" repeats 10 000 times (~540 KB saved).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```
