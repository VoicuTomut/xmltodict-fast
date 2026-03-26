# CLAUDE.md — Agent Instructions for xmltodict

This file is loaded automatically by Claude Code. Read it before making any change.

---

## What this codebase is

Single-file Python library (`xmltodict.py`, ~650 lines) that converts XML ↔ dict.
Two public functions: `parse()` and `unparse()`. One public exception: `ParsingInterrupted`.
Everything else is internal.

---

## What you must never change without explicit instruction

- **`disable_entities=True` default** — security default, prevents XML bomb attacks. Do not flip it.
- **`parser.ordered_attributes = True`** — required for deterministic attribute ordering. Do not remove it.
- **The SAX stack in `_DictSAXHandler`** — the `self.stack`, `self.item`, `self.data` trio is the core of the parser. Read ARCHITECTURE.md before touching it.
- **`_validate_comment` double-hyphen check** — `--` inside XML comments is illegal per the XML spec. Do not weaken it.
- **The streaming memory fix** — `endElement` intentionally drops streamed items instead of attaching them to the parent. This prevents unbounded memory growth. Do not "simplify" it.

---

## Known latent bugs — do NOT silently fix these

Two bugs exist in `_validate_name` that are intentionally left as-is and documented in `KNOWN_ISSUES.md`:

1. **Empty element name** — `unparse({"": "x"})` passes validation and emits `<>x</>` (invalid XML).
2. **Empty attribute name** — `unparse({"a": {"@": "x"}})` strips the `@` prefix and emits `<a ="x">` (invalid XML).

The tests in `tests/test_extended.py` (`TestUnparseCrashVectors`) deliberately document the *current broken behavior*, not the desired behavior. If you fix these bugs, update those two tests to expect `ValueError` instead.

---

## Test suite structure

| File | Purpose |
|---|---|
| `tests/test_xmltodict.py` | Original upstream tests for `parse()`. Never delete or weaken. |
| `tests/test_dicttoxml.py` | Original upstream tests for `unparse()`. Never delete or weaken. |
| `tests/test_extended.py` | Regression guard + crash-prevention suite added during revival. |

`TestParseCrashVectors` and `TestUnparseCrashVectors` in `test_extended.py` are crash-prevention tests — they document exact failure modes. Some tests in those classes document *bugs*, not desired behavior. Read the comments before changing assertions.

When adding new behavior, add tests to `test_extended.py`. Do not modify the original two files.

---

## Coding conventions

- Python 3.9+.
- Single file: all logic lives in `xmltodict.py`. Do not split it.
- No new dependencies. The library uses only the standard library.
- All new public kwargs must have defaults that preserve backwards compatibility.
- Commits: Conventional Commits format (`fix:`, `feat:`, `test:`, `docs:`).
- `_validate_name(value, kind)` — `kind` must be `"element"` or `"attribute"` for error messages.
- `_convert_value_to_string` handles `bool`, `bytes`, `bytearray`, `memoryview`, and arbitrary types. Add new type handling there, not inline.

---

## Things that look wrong but are intentional

- `force_cdata` and `force_list` accept `bool | tuple | callable` — this is intentional flexibility, not overengineering.
- `postprocessor` returning `None` silently drops the key — this is a documented feature.
- `item_callback` returning falsy raises `ParsingInterrupted` — intentional streaming interrupt mechanism.
- Empty lists in `unparse` are silently skipped — documented behavior, not a bug.
- `unparse({"#comment": ...}, full_document=False)` with no root does not raise — comments are not root elements.

---

## Before any change, run

```bash
python -m pytest tests/ --tb=short
```

Zero failures required. If you break a test, fix the code, not the test.
