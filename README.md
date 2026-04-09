# xmltodict-fast

**xmltodict** — now with a Rust acceleration layer. Drop-in replacement: same API, same behaviour, dramatically faster.

[![Tests](https://github.com/martinblech/xmltodict/actions/workflows/test.yml/badge.svg)](https://github.com/martinblech/xmltodict/actions/workflows/test.yml)

```python
>>> import xmltodict, json
>>> print(json.dumps(xmltodict.parse("""
...  <mydocument has="an attribute">
...    <and>
...      <many>elements</many>
...      <many>more elements</many>
...    </and>
...    <plus a="complex">
...      element as well
...    </plus>
...  </mydocument>
...  """), indent=4))
{
    "mydocument": {
        "@has": "an attribute",
        "and": {
            "many": [
                "elements",
                "more elements"
            ]
        },
        "plus": {
            "@a": "complex",
            "#text": "element as well"
        }
    }
}
```

---

## What changed from the original

The original `xmltodict` is a well-loved, zero-dependency library that converts XML to Python dicts and back. This fork keeps every public API unchanged and adds a Rust extension module (PyO3 + [quick-xml](https://github.com/tafia/quick-xml)) that replaces the hot paths:

| Path | Original | This fork |
|------|----------|-----------|
| `parse()` | Python + expat | Rust (quick-xml) |
| `unparse()` | Python + XMLGenerator | Rust |
| `parse(item_depth=N, item_callback=...)` | Python (streaming) | Rust |

If the Rust extension cannot be loaded (e.g., unsupported platform, PyPy), the library transparently falls back to the original Python implementation — no code changes needed.

---

## Benchmarks — Rust vs pure Python

Measured on Apple Silicon (M-series); 20 fresh-subprocess runs per fixture, median reported. Python 3.13.

### parse() throughput

| Fixture | Pure Python | Rust | Speedup |
|---------|-------------|------|---------|
| small.xml (~1 KB) | 14 MB/s | 31 MB/s | **2.1×** |
| medium.xml (~600 KB) | 40 MB/s | 90 MB/s | **2.2×** |
| large.xml (~7 MB) | 27 MB/s | 73 MB/s | **2.8×** |
| wide.xml (~800 KB, flat) | 24 MB/s | 60 MB/s | **2.4×** |
| namespaced.xml (~300 KB) | 36 MB/s | 80 MB/s | **2.2×** |
| deep.xml (~500 KB, 500 levels) | 444 MB/s | 177 MB/s | **0.4×** |

> **Note on deep nesting:** The Rust path is slower on pathologically deep XML (500+ nesting levels) due to per-element PyO3 overhead. Python's expat handles this case in C with lower per-element cost. Most real-world XML has moderate nesting depth where Rust wins comfortably.

### unparse() throughput

| Fixture | Pure Python | Rust | Speedup |
|---------|-------------|------|---------|
| medium.xml (~600 KB) | 31 MB/s | 224 MB/s | **7.2×** |
| large.xml (~7 MB) | 21 MB/s | 166 MB/s | **8.0×** |
| wide.xml (~800 KB) | 17 MB/s | 99 MB/s | **5.9×** |

### When to use the Rust backend

The Rust extension is used automatically when available. It provides the best speedup for:

- **`unparse()` — always faster** (6–8× speedup on all inputs)
- **`parse()` with typical XML** — 2.1–2.8× faster for documents with moderate nesting

The Rust path falls back to Python automatically for:

- Deeply nested XML (500+ levels) where expat's C-level SAX is faster
- Features not yet implemented in Rust: `process_namespaces`, `process_comments`, `postprocessor`, callable `force_list`/`force_cdata`, non-default `dict_constructor`
- Generator inputs (processed incrementally by Python's SAX parser)
- PyPy or platforms without a pre-built wheel

To force the pure-Python path:

```python
import xmltodict
xmltodict._RUST_AVAILABLE = False   # must be set before any parse/unparse call
```

---

## Installation

```sh
pip install xmltodict
```

The package ships pre-built wheels for Linux, macOS, and Windows (x86-64 and arm64). If no wheel matches your platform, pip falls back to building from source (requires a Rust toolchain: `rustup`).

---

## Quick start

```python
import xmltodict

# XML → dict
result = xmltodict.parse("<root><item id='1'>hello</item></root>")
# {'root': {'item': {'@id': '1', '#text': 'hello'}}}

# dict → XML
xml = xmltodict.unparse(result, pretty=True)
```

---

## Streaming large files

Use `item_depth` and `item_callback` to process large XML files without building the full document tree in memory. Each item is emitted to the callback and discarded.

```python
def handle_article(path, item):
    print(item["title"])
    return True  # return falsy to stop early

with open("enwiki-pages-articles.xml", "rb") as f:
    xmltodict.parse(f, item_depth=2, item_callback=handle_article)
```

`item_callback` receives:
- `path` — list of `(element_name, attributes_or_None)` tuples from the root down to (but not including) the current item.
- `item` — the fully parsed dict for the current element.

Return `False` (or any falsy value) to stop parsing early. `ParsingInterrupted` is raised to signal the stop — catch it if needed:

```python
from xmltodict import ParsingInterrupted

try:
    xmltodict.parse(data, item_depth=2, item_callback=my_callback)
except ParsingInterrupted:
    pass  # stopped by callback returning False
```

---

## Namespace support

By default, namespace declarations are treated as regular attributes. Pass `process_namespaces=True` to expand them:

```python
xml = """
<root xmlns="http://defaultns.com/"
      xmlns:a="http://a.com/">
  <x>1</x>
  <a:y>2</a:y>
</root>
"""

xmltodict.parse(xml, process_namespaces=True)
# {'http://defaultns.com/:root': {'http://defaultns.com/:x': '1',
#                                  'http://a.com/:y': '2'}}
```

Collapse or skip namespaces with the `namespaces` dict:

```python
xmltodict.parse(xml, process_namespaces=True, namespaces={
    "http://defaultns.com/": None,   # skip — strip the namespace
    "http://a.com/": "ns_a",         # shorten to prefix
})
# {'root': {'x': '1', 'ns_a:y': '2'}}
```

---

## Roundtripping

```python
mydict = {
    "response": {
        "status": "good",
        "last_updated": "2024-01-01T00:00:00Z",
    }
}
print(xmltodict.unparse(mydict, pretty=True))
```

```xml
<?xml version="1.0" encoding="utf-8"?>
<response>
	<status>good</status>
	<last_updated>2024-01-01T00:00:00Z</last_updated>
</response>
```

Attributes and CDATA use configurable prefixes (`attr_prefix='@'`, `cdata_key='#text'` by default):

```python
xmltodict.unparse({"text": {"@color": "red", "#text": "hello"}}, pretty=True)
# <text color="red">hello</text>
```

---

## API Reference

### `xmltodict.parse(xml_input, **kwargs)`

| Parameter | Default | Description |
|-----------|---------|-------------|
| `xml_input` | — | String, bytes, file-like object, or generator of strings |
| `encoding` | `None` | Input encoding (auto-detected if None) |
| `process_namespaces` | `False` | Expand XML namespace URIs |
| `namespace_separator` | `':'` | Separator between namespace URI and local name |
| `disable_entities` | `True` | Block entity expansion (security default — do not disable) |
| `process_comments` | `False` | Include XML comments in output |
| `xml_attribs` | `True` | Include element attributes |
| `attr_prefix` | `'@'` | Prefix for attribute keys |
| `cdata_key` | `'#text'` | Key for element text content |
| `force_cdata` | `False` | Force text-as-CDATA for all, selected, or matched elements |
| `cdata_separator` | `''` | Join string for adjacent text chunks |
| `postprocessor` | `None` | `fn(path, key, value) → (key, value)` applied to every item; returning `None` drops the item |
| `dict_constructor` | `dict` | Dict class to use (e.g. `OrderedDict`) |
| `strip_whitespace` | `True` | Trim whitespace in text nodes |
| `namespaces` | `None` | Namespace URI → prefix mapping (requires `process_namespaces=True`) |
| `force_list` | `None` | Force list wrapping for all, selected, or matched elements |
| `item_depth` | `0` | Element depth at which to call `item_callback` (0 = disabled) |
| `item_callback` | `lambda *a: True` | Called with `(path, item)` for each element at `item_depth` |
| `comment_key` | `'#comment'` | Key used for comments when `process_comments=True` |

### `xmltodict.unparse(input_dict, **kwargs)`

| Parameter | Default | Description |
|-----------|---------|-------------|
| `input_dict` | — | Dict to convert |
| `output` | `None` | File-like object; returns string if `None` |
| `encoding` | `'utf-8'` | Output encoding |
| `full_document` | `True` | Prepend `<?xml ...?>` declaration |
| `short_empty_elements` | `False` | Use `<tag/>` for empty elements |
| `attr_prefix` | `'@'` | Attribute key prefix |
| `cdata_key` | `'#text'` | Text content key |
| `pretty` | `False` | Indent output |
| `indent` | `'\t'` | Indent string (or integer number of spaces) |
| `newl` | `'\n'` | Newline string |
| `expand_iter` | `None` | Tag for items in nested lists (breaks roundtripping) |

---

## Examples

### force_cdata — selective CDATA wrapping

```python
xml = "<a><b>data1</b><c>data2</c></a>"

# Only wrap specific elements
xmltodict.parse(xml, force_cdata=("b",))
# {'a': {'b': {'#text': 'data1'}, 'c': 'data2'}}

# All elements
xmltodict.parse(xml, force_cdata=True)
# {'a': {'b': {'#text': 'data1'}, 'c': {'#text': 'data2'}}}

# Callable
xmltodict.parse(xml, force_cdata=lambda path, key, val: key == "b")
# {'a': {'b': {'#text': 'data1'}, 'c': 'data2'}}
```

### force_list — consistent list output

Useful when an element may appear once or multiple times and you always want a list:

```python
xml = "<a><item>one</item></a>"
xmltodict.parse(xml, force_list=("item",))
# {'a': {'item': ['one']}}   ← always a list, even for a single element
```

### postprocessor — transform values on the fly

```python
def int_postprocessor(path, key, value):
    try:
        return key, int(value)
    except (ValueError, TypeError):
        return key, value

xmltodict.parse("<root><count>42</count></root>", postprocessor=int_postprocessor)
# {'root': {'count': 42}}
```

### Nested lists with expand_iter

```python
mydict = {"line": {"points": [[1, 5], [2, 6]]}}
print(xmltodict.unparse(mydict, pretty=True, expand_iter="coord"))
```

```xml
<?xml version="1.0" encoding="utf-8"?>
<line>
	<points>
		<coord>1</coord>
		<coord>5</coord>
	</points>
	<points>
		<coord>2</coord>
		<coord>6</coord>
	</points>
</line>
```

---

## Security

- **`disable_entities=True`** (default) blocks XML entity expansion (billion-laughs / XML-bomb attacks). Do not disable this.
- **`_validate_name`** guards element and attribute names during `unparse()` to prevent tag-injection attacks.
- **`_validate_comment`** rejects `--` inside XML comments (illegal per spec).

A CVE (CVE-2025-9375) was filed against `xmltodict` but is [disputed](https://github.com/martinblech/xmltodict/issues/377#issuecomment-3255691923). The root issue is in Python's `xml.sax.saxutils.XMLGenerator`, which does not validate element names. The same behaviour exists throughout the standard library. The disclosure timeline (10 days from first contact to publication) did not allow a maintainer response.

---

## Compatibility notes

- Python 3.9+
- Falls back to pure Python automatically when the Rust extension is unavailable (PyPy, unsupported architectures, source installs without Rust)
- Full backwards compatibility with the original `xmltodict` API
- `xmltodict.py` — the original single-file implementation — is preserved as the fallback

---

## License

MIT. Copyright (C) 2012 Martin Blech and individual contributors.
Rust acceleration layer Copyright (C) 2025 Andrei Voicu Tomuț.
