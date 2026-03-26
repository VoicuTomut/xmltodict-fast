# Architecture

How xmltodict works internally. Read this before touching `xmltodict.py`.

---

## Overview

```
xmltodict.py
├── ParsingInterrupted          exception
├── _DictSAXHandler             SAX event handler (parse side)
│   ├── startElement / endElement / characters / comments
│   ├── push_data               accumulates key/value into current item
│   ├── _should_force_list      evaluates force_list arg
│   └── _should_force_cdata     evaluates force_cdata arg
├── parse()                     public entry point — wires expat to handler
├── _convert_value_to_string    used by unparse side
├── _validate_name              guards element/attribute names
├── _validate_comment           guards comment text
├── _process_namespace          namespace prefix expansion for unparse
├── _emit()                     recursive unparse engine
├── _XMLGenerator               thin subclass adding .comment() to XMLGenerator
└── unparse()                   public entry point — drives _emit
```

---

## Parse side: the SAX stack

`_DictSAXHandler` implements a push-down automaton using three instance variables:

```
self.stack  — list of (item, data) snapshots, one per open element
self.item   — dict being built for the current element (or None)
self.data   — list of character data chunks for the current element
```

**On `startElement`:**
1. Push `(self.item, self.data)` onto `self.stack`.
2. Start fresh: `self.item = attrs_dict_or_None`, `self.data = []`.

**On `endElement`:**
1. Collapse `self.data` into a string (joined by `cdata_separator`).
2. Merge with `self.item` if attrs exist.
3. Pop the parent context from `self.stack`.
4. Call `push_data(parent_item, element_name, collapsed_child)` to attach
   the finished child to its parent.

**Streaming shortcut (item_depth > 0):**
When `len(self.path) == self.item_depth`, instead of attaching the finished
item to its parent, `endElement` calls `item_callback` and then **discards**
the item. This is what keeps streaming memory-constant. Do not restore the
parent reference after the callback — that was the original memory leak.

**`push_data` duplicate-key logic:**
- First occurrence of a key: stored as a scalar.
- Second occurrence: wrapped into `[first, second]`.
- Third+: appended to the existing list.
- If `force_list` is active for this key: stored as `[value]` on first occurrence.

---

## Unparse side: recursive `_emit`

`_emit(key, value, content_handler, depth, ...)` recurses into nested dicts.

**The value normalization loop:**
```
if value is not iterable or is str/bytes/dict:
    wrap in [value]          # ensure we always iterate
for v in value:
    if v is None → {}
    elif v is not dict/str:
        if expand_iter applies → {expand_iter: v}
        else → str(v)
    if v is str → {cdata_key: v}   # treat plain string as cdata
    # now v is a dict — split into attrs, cdata, children
    emit startElement(attrs)
    recurse for each child
    emit characters(cdata)
    emit endElement
```

**Why `full_document=True` blocks multiple roots:**
The XML spec allows only one root element. `_emit` raises `ValueError` if
`depth == 0 and index > 0`. The `comment_key` is exempt because XML comments
are not root elements.

---

## Namespace handling

**Parsing (`process_namespaces=True`):**
Expat is created with `namespace_separator` as the separator character.
Element names arrive as `"http://ns.com/:localname"`. `_build_name` maps the
URI to a short prefix using the `namespaces` dict.

**Unparsing:**
`_process_namespace` does the reverse — expands short prefixes back to URIs
before emitting. `@xmlns` keys in the dict emit `xmlns:prefix="uri"` attributes.

---

## Security surface

| Default | Why |
|---|---|
| `disable_entities=True` | Prevents billion-laughs / XML bomb attacks |
| `parser.ordered_attributes = True` | Deterministic output; not security-related |
| `_validate_name` | Prevents attribute/element injection into the tag context |
| `_validate_comment` | `--` inside comments is illegal XML and would break parsers |

**Do not change any of these defaults.**

---

## Known fragile areas

1. `_validate_name` does not reject empty string — see `KNOWN_ISSUES.md #1`.
2. No cycle detection in `_emit` — see `KNOWN_ISSUES.md #5`.
3. `_DictSAXHandler` is stateful; do not share instances across threads or
   across multiple `parse()` calls.
