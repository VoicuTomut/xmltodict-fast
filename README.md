# xmltodict-rs

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
| `parse(item_depth=N, item_callback=...)` | Python (streaming) | Rust (constant 2 KB overhead) |

If the Rust extension cannot be loaded (e.g., unsupported platform, PyPy), the library transparently falls back to the original Python implementation — no code changes needed.

---

## Benchmarks — Rust vs pure Python

Measured on a modern laptop; median of 5 runs per fixture.

### parse() throughput

| Fixture | Pure Python | Rust | Speedup |
|---------|-------------|------|---------|
| small.xml (~1 KB) | 14 MB/s | 40 MB/s | **2.8×** |
| medium.xml (~600 KB) | 40 MB/s | 93 MB/s | **2.3×** |
| large.xml (~7 MB) | 28 MB/s | 76 MB/s | **2.7×** |
| wide.xml (~800 KB, flat) | 26 MB/s | 64 MB/s | **2.5×** |
| namespaced.xml (~300 KB) | 38 MB/s | 84 MB/s | **2.2×** |

### Per-element cost

| N | Pure Python | Rust | Speedup |
|---|-------------|------|---------|
| 100 elements | 2.82 µs/elem | 1.31 µs/elem | **2.2×** |
| 10 000 elements | 2.88 µs/elem | 1.07 µs/elem | **2.7×** |
| 50 000 elements | 2.92 µs/elem | 1.10 µs/elem | **2.7×** |

### Memory usage (non-streaming)

| Fixture | Pure Python | Rust | Improvement |
|---------|-------------|------|-------------|
| medium.xml (~600 KB) | 4.1× input | 2.0× input | **51% less** |
| large.xml (~7 MB) | 3.1× input | 1.9× input | **39% less** |
| wide.xml (~800 KB) | 7.5× input | 4.0× input | **47% less** |

### Streaming memory overhead (`item_depth=2, item_callback=noop`)

| Fixture | Pure Python | Rust | Improvement |
|---------|-------------|------|-------------|
| medium.xml (~600 KB) | 1 149 KB | **2 KB** | **478×** |
| large.xml (~7 MB) | 4 132 KB | **2 KB** | **2 066×** |
| wide.xml (~800 KB) | 1 561 KB | **2 KB** | **678×** |

Streaming overhead in Rust is constant regardless of file size. That is the whole point.

### unparse() throughput

| Fixture | Pure Python | Rust | Speedup |
|---------|-------------|------|---------|
| medium.xml (~600 KB) | 34 MB/s | 237 MB/s | **7.0×** |
| large.xml (~7 MB) | 22 MB/s | 181 MB/s | **8.1×** |
| wide.xml (~800 KB) | 18 MB/s | 106 MB/s | **5.8×** |

---

## Installation

```sh
pip install xmltodict
```

The package ships pre-built wheels for Linux, macOS, and Windows (x86-64 and arm64). If no wheel matches your platform, pip falls back to building from source (requires a Rust toolchain: `rustup`).

To force the pure-Python fallback:

```python
import xmltodict
xmltodict._RUST_AVAILABLE = False   # must be set before any parse/unparse call
```

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

Use `item_depth` and `item_callback` to process huge XML files with constant memory.
Memory overhead stays at **~2 KB** regardless of file size.

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
