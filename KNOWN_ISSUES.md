# Known Issues

Issues that are documented but not yet fixed. Do not silently patch these —
any fix must update the corresponding tests in `tests/test_extended.py`.

---

## 1. `_validate_name` does not reject empty string

**Location:** `xmltodict.py` → `_validate_name(value, kind)`

**Trigger:**
```python
unparse({"": "x"}, full_document=False)
# Returns: '<>x</>'  ← invalid XML
```

**Root cause:** `_validate_name` checks for illegal characters but has no `if not value` guard.

**Impact:** Silently produces malformed XML that most parsers will reject downstream.

**Test that documents this:**
`tests/test_extended.py::TestUnparseCrashVectors::test_empty_element_name_produces_invalid_xml`

**Fix:** Add `if not value: raise ValueError(f"{kind} name must not be empty")` as the first check in `_validate_name`. Then update the test to expect `ValueError`.

---

## 2. Empty attribute name after prefix stripping

**Location:** `xmltodict.py` → `_emit()`

**Trigger:**
```python
unparse({"a": {"@": "x"}}, full_document=False)
# Returns: '<a ="x"></a>'  ← invalid XML
```

**Root cause:** When a key is exactly the `attr_prefix` (default `"@"`), stripping it produces an empty string `""`, which passes `_validate_name` (see issue #1 above) and is forwarded to `XMLGenerator` as an attribute name.

**Impact:** Silently produces malformed XML.

**Test that documents this:**
`tests/test_extended.py::TestUnparseCrashVectors::test_empty_attr_name_after_prefix_strip`

**Fix:** Fixing issue #1 also fixes this — the empty string will be caught by `_validate_name` before reaching `XMLGenerator`. Update the test to expect `ValueError`.

---

## 3. No guard on `parse(None)`

**Trigger:**
```python
parse(None)
# Raises: TypeError: a bytes-like object is required, not 'NoneType'
```

**Root cause:** `None` is not caught before being passed to `expat.Parse()`, so the error message comes from expat internals rather than xmltodict.

**Impact:** Confusing error message. Not a data-loss risk.

**Test:** `tests/test_extended.py::TestParseCrashVectors::test_none_input_raises_typeerror`

**Fix:** Add `if xml_input is None: raise TypeError("xml_input must not be None")` at the top of `parse()`. Update the test match string.

---

## 4. No guard on `unparse(non-dict)`

**Trigger:**
```python
unparse(None)   # AttributeError: 'NoneType' object has no attribute 'items'
unparse([])     # AttributeError: 'list' object has no attribute 'items'
unparse("str")  # AttributeError: 'str' object has no attribute 'items'
```

**Root cause:** `unparse` calls `input_dict.items()` with no type check.

**Impact:** Confusing `AttributeError` instead of a clear `TypeError`.

**Tests:** `tests/test_extended.py::TestUnparseCrashVectors::test_none_input_raises_attributeerror` etc.

**Fix:** Add `if not isinstance(input_dict, dict): raise TypeError(...)` at the top of `unparse()`. Update tests to expect `TypeError`.

---

## 5. Circular references cause `RecursionError`

**Trigger:**
```python
d = {}
d["a"] = d
unparse(d)  # RecursionError
```

**Root cause:** `_emit` recurses into child dicts with no cycle detection.

**Impact:** Unrecoverable crash — `RecursionError` in Python cannot be caught reliably in all contexts.

**Test:** `tests/test_extended.py::TestUnparseCrashVectors::test_circular_reference_raises_recursion_error`

**Fix:** Add a `seen` set tracking object ids through `_emit` recursion. Raise `ValueError` on cycle detection. This is a deeper change — do not attempt without updating tests.
