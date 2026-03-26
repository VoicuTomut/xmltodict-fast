use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString, PyTuple};
use quick_xml::events::Event;
use quick_xml::Reader;
use std::collections::HashMap;

struct Frame {
    name: String,
    item: Option<Py<PyDict>>,
    data: Vec<String>,
}

// ── key interning ─────────────────────────────────────────────────────────────

/// Per-parse-call cache: reuse the same PyString object for every occurrence of
/// a given key string.  Cuts allocations dramatically on wide documents where
/// the same element names / attribute keys repeat thousands of times.
type KeyCache = HashMap<String, Py<PyString>>;

fn intern_key(py: Python<'_>, cache: &mut KeyCache, key: &str) -> Py<PyString> {
    if let Some(cached) = cache.get(key) {
        cached.clone_ref(py)
    } else {
        let py_str = PyString::new(py, key).unbind();
        cache.insert(key.to_string(), py_str.clone_ref(py));
        py_str
    }
}

// ── value interning (size-capped) ─────────────────────────────────────────────

/// Only short values can be cached (long strings are rarely repeated).
const VALUE_LEN_MAX: usize = 32;
/// Stop inserting new entries once the cache reaches this size.
/// Cache hits still work after the cap — no correctness issue, just missed savings.
const VALUE_CACHE_MAX: usize = 512;

/// Try to return a cached PyString for `value`.  Returns None when the value is
/// too long to be worth caching, or when the cache is full and `value` is new.
/// The caller falls back to a fresh PyString::new in that case.
fn try_intern_value(py: Python<'_>, cache: &mut KeyCache, value: &str) -> Option<Py<PyString>> {
    if value.len() > VALUE_LEN_MAX {
        return None;
    }
    if let Some(cached) = cache.get(value) {
        return Some(cached.clone_ref(py));
    }
    if cache.len() >= VALUE_CACHE_MAX {
        return None;
    }
    let py_str = PyString::new(py, value).unbind();
    cache.insert(value.to_string(), py_str.clone_ref(py));
    Some(py_str)
}

// ── helpers ───────────────────────────────────────────────────────────────────

fn push_data(
    py: Python<'_>,
    item: Option<Py<PyDict>>,
    key: &str,
    data: Option<PyObject>,
    force_list: &Option<Vec<String>>,
    key_cache: &mut KeyCache,
) -> PyResult<Py<PyDict>> {
    let d = match item {
        Some(d) => d,
        None => PyDict::new(py).unbind(),
    };
    let dict = d.bind(py);

    let should_force = match force_list {
        Some(ref keys) => keys.iter().any(|k| k == key),
        None => false,
    };

    // get_item with &str is fine — PyO3 compares via __eq__ against existing keys
    match dict.get_item(key)? {
        Some(existing) => {
            if let Ok(lst) = existing.downcast::<PyList>() {
                lst.append(data.unwrap_or_else(|| py.None()))?;
            } else {
                let lst = PyList::new(py, [existing.clone()])?;
                lst.append(data.unwrap_or_else(|| py.None()))?;
                let py_key = intern_key(py, key_cache, key);
                dict.set_item(py_key.bind(py), lst)?;
            }
        }
        None => {
            let py_key = intern_key(py, key_cache, key);
            if should_force {
                let lst = PyList::empty(py);
                lst.append(data.unwrap_or_else(|| py.None()))?;
                dict.set_item(py_key.bind(py), lst)?;
            } else {
                dict.set_item(py_key.bind(py), data.unwrap_or_else(|| py.None()))?;
            }
        }
    }
    Ok(d)
}

fn xml_error(msg: impl std::fmt::Display) -> PyErr {
    // Raise as ValueError to match Python expat behaviour (tests catch Exception broadly).
    pyo3::exceptions::PyValueError::new_err(format!("XML parse error: {msg}"))
}

fn build_attrs_dict(
    py: Python<'_>,
    attributes: quick_xml::events::attributes::Attributes<'_>,
    attr_prefix: &str,
    reader: &Reader<&[u8]>,
    key_cache: &mut KeyCache,
    value_cache: &mut KeyCache,
    intern_values: bool,
) -> PyResult<Option<Py<PyDict>>> {
    let decoder = reader.decoder();
    let mut dict: Option<Py<PyDict>> = None;

    for attr in attributes {
        let attr = attr.map_err(xml_error)?;
        // Use the FULL key name (preserves xmlns: prefix), not local_name()
        let key_str = std::str::from_utf8(attr.key.as_ref())
            .map_err(|_| xml_error("invalid UTF-8 in attribute name"))?;
        let full_key = format!("{attr_prefix}{key_str}");
        let val = attr.decode_and_unescape_value(decoder).map_err(xml_error)?;
        let d = dict.get_or_insert_with(|| PyDict::new(py).unbind());
        let py_key = intern_key(py, key_cache, &full_key);
        let val_py: PyObject = if intern_values {
            if let Some(s) = try_intern_value(py, value_cache, val.as_ref()) {
                s.into_bound(py).into()
            } else {
                PyString::new(py, val.as_ref()).into()
            }
        } else {
            PyString::new(py, val.as_ref()).into()
        };
        d.bind(py).set_item(py_key.bind(py), val_py)?;
    }

    Ok(dict)
}

/// Build an unprefixed attrs dict for the streaming path: keys are raw XML
/// attribute names (no attr_prefix applied), matching the Python SAX handler
/// which stores raw attrs in self.path before applying any prefix.
fn build_raw_attrs_dict(
    py: Python<'_>,
    attributes: quick_xml::events::attributes::Attributes<'_>,
    reader: &Reader<&[u8]>,
) -> PyResult<Option<Py<PyDict>>> {
    let decoder = reader.decoder();
    let mut dict: Option<Py<PyDict>> = None;

    for attr in attributes {
        let attr = attr.map_err(xml_error)?;
        let key_str = std::str::from_utf8(attr.key.as_ref())
            .map_err(|_| xml_error("invalid UTF-8 in attribute name"))?;
        let val = attr.decode_and_unescape_value(decoder).map_err(xml_error)?;
        let d = dict.get_or_insert_with(|| PyDict::new(py).unbind());
        d.bind(py).set_item(key_str, val.as_ref())?;
    }

    Ok(dict)
}

/// Build the Python list passed to item_callback: [(name, attrs_or_None), ...]
/// Mirrors the self.path list in Python's _DictSAXHandler.
fn build_callback_path(
    py: Python<'_>,
    path: &[(String, Option<Py<PyDict>>)],
) -> PyResult<Py<PyList>> {
    let list = PyList::empty(py);
    for (name, raw_attrs) in path {
        let name_obj = PyString::new(py, name);
        let attrs_obj: Bound<'_, PyAny> = match raw_attrs {
            Some(d) => d.bind(py).clone().into_any(),
            None => py.None().into_bound(py),
        };
        let tup = PyTuple::new(py, [name_obj.into_any(), attrs_obj])?;
        list.append(tup)?;
    }
    Ok(list.unbind())
}

fn decode_name(bytes: &[u8]) -> PyResult<String> {
    std::str::from_utf8(bytes)
        .map(|s| s.to_string())
        .map_err(|_| xml_error("invalid UTF-8 in element name"))
}

fn join_data(parts: Vec<String>, sep: &str, strip: bool) -> Option<String> {
    if parts.is_empty() {
        return None;
    }
    let joined = parts.join(sep);
    if strip {
        let s = joined.trim().to_string();
        if s.is_empty() { None } else { Some(s) }
    } else {
        Some(joined)
    }
}

// ── public entry point ────────────────────────────────────────────────────────

#[pyfunction]
#[pyo3(signature = (
    xml_input,
    xml_attribs = true,
    attr_prefix = "@",
    cdata_key = "#text",
    force_cdata = false,
    cdata_separator = "",
    strip_whitespace = true,
    force_list = None,
    disable_entities = true,
    item_depth = 0,
    item_callback = None,
))]
pub fn parse(
    py: Python<'_>,
    xml_input: &Bound<'_, PyAny>,
    xml_attribs: bool,
    attr_prefix: &str,
    cdata_key: &str,
    force_cdata: bool,
    cdata_separator: &str,
    strip_whitespace: bool,
    force_list: Option<Vec<String>>,
    disable_entities: bool,
    item_depth: usize,
    item_callback: Option<Py<PyAny>>,
) -> PyResult<PyObject> {
    // Accept bytes / bytearray / str
    let xml_bytes: Vec<u8> = if let Ok(b) = xml_input.extract::<Vec<u8>>() {
        b
    } else if let Ok(s) = xml_input.downcast::<PyString>() {
        s.to_str()?.as_bytes().to_vec()
    } else {
        return Err(pyo3::exceptions::PyTypeError::new_err(
            "xml_input must be str or bytes",
        ));
    };

    if xml_bytes.is_empty() {
        return Err(xml_error("no element found"));
    }
    if xml_bytes.contains(&0u8) {
        return Err(xml_error("invalid token"));
    }

    let mut reader = Reader::from_reader(xml_bytes.as_slice());
    // When strip_whitespace is enabled, let quick-xml trim each text chunk and
    // suppress empty-after-trim text events entirely.  This avoids generating
    // Event::Text at all for pure-whitespace content (e.g. the 97% whitespace
    // indentation in deep.xml), which is much cheaper than allocating and
    // discarding strings ourselves.  When strip_whitespace is false we still
    // need every byte, so we fall back to no trimming.
    reader.config_mut().trim_text(strip_whitespace);

    let mut stack: Vec<Frame> = Vec::new();
    // Track completed root elements so we can raise on multiple roots.
    let mut root_count: usize = 0;
    let mut root_item: Option<Py<PyDict>> = None;

    // Per-call key cache: element names and attribute keys repeat across elements.
    let mut key_cache: KeyCache = HashMap::new();
    // Per-call value cache: short, repeated values (e.g. enum-like attrs).
    // Skipped in streaming mode: streamed items are discarded after each callback,
    // so value reuse across elements is minimal and the cache itself would add
    // ~30 KB of overhead (512 live PyStrings) that shows up in streaming benchmarks.
    let mut value_cache: KeyCache = HashMap::new();

    // Streaming state
    let streaming = item_depth > 0;
    // Only intern values in non-streaming mode where the full document lives in memory.
    let intern_values = !streaming;
    let mut depth: usize = 0;
    // Path for streaming callbacks: (element_name, raw_attrs_or_None)
    let mut stream_path: Vec<(String, Option<Py<PyDict>>)> = Vec::new();

    loop {
        let event = reader.read_event().map_err(xml_error)?;

        match event {
            Event::Start(ref e) => {
                let name = decode_name(e.name().as_ref())?;

                // When streaming: build raw (unprefixed) attrs for the path entry,
                // then build prefixed attrs for the item dict.
                // When not streaming: build only prefixed attrs (or nothing).
                let (item, path_raw_attrs): (Option<Py<PyDict>>, Option<Py<PyDict>>) =
                    if streaming {
                        // Raw attrs (no prefix) for path — always built when streaming
                        let raw = build_raw_attrs_dict(py, e.attributes(), &reader)?;
                        // Prefixed attrs for the item dict
                        let prefixed = if xml_attribs {
                            build_attrs_dict(py, e.attributes(), attr_prefix, &reader, &mut key_cache, &mut value_cache, intern_values)?
                        } else {
                            None
                        };
                        (prefixed, raw)
                    } else if xml_attribs {
                        (build_attrs_dict(py, e.attributes(), attr_prefix, &reader, &mut key_cache, &mut value_cache, intern_values)?, None)
                    } else {
                        // Consume attributes to avoid errors, but discard them
                        for attr in e.attributes() {
                            attr.map_err(xml_error)?;
                        }
                        (None, None)
                    };

                if streaming {
                    stream_path.push((name.clone(), path_raw_attrs));
                    depth += 1;
                }

                stack.push(Frame {
                    name,
                    item,
                    data: Vec::new(),
                });
            }

            Event::End(_) => {
                let frame = stack.pop().ok_or_else(|| xml_error("unexpected end element"))?;
                let name = frame.name;
                let item = frame.item;
                let data_parts = frame.data;

                let data: Option<String> = join_data(data_parts, cdata_separator, strip_whitespace);

                // force_cdata: wrap text in {cdata_key: text} if we have text but no attrs
                let item = if data.is_some() && force_cdata && item.is_none() {
                    Some(PyDict::new(py).unbind())
                } else {
                    item
                };

                let value: Option<PyObject> = if let Some(ref d) = item {
                    if let Some(ref text) = data {
                        let ck = intern_key(py, &mut key_cache, cdata_key);
                        let text_py: PyObject = if intern_values {
                            if let Some(s) = try_intern_value(py, &mut value_cache, text) {
                                s.into_bound(py).into()
                            } else {
                                PyString::new(py, text).into()
                            }
                        } else {
                            PyString::new(py, text).into()
                        };
                        d.bind(py).set_item(ck.bind(py), text_py)?;
                    }
                    Some(d.bind(py).clone().into())
                } else {
                    data.map(|s| {
                        if intern_values {
                            if let Some(py_str) = try_intern_value(py, &mut value_cache, &s) {
                                return py_str.into_bound(py).into();
                            }
                        }
                        PyString::new(py, &s).into()
                    })
                };

                if streaming && depth == item_depth {
                    // Fire the streaming callback instead of attaching to parent
                    let path_list = build_callback_path(py, &stream_path)?;
                    if let Some(ref cb) = item_callback {
                        let item_val: PyObject = value.unwrap_or_else(|| py.None());
                        let result = cb.bind(py).call1((path_list, item_val))?;
                        if !result.is_truthy()? {
                            return Err(crate::ParsingInterrupted::new_err(""));
                        }
                    }
                    stream_path.pop();
                    depth -= 1;
                    // Don't attach to parent — the item has been consumed by the callback
                } else {
                    if let Some(parent) = stack.last_mut() {
                        let parent_item = parent.item.take();
                        let new_item =
                            push_data(py, parent_item, &name, value, &force_list, &mut key_cache)?;
                        parent.item = Some(new_item);
                    } else {
                        // Root element closed (not at streaming depth)
                        root_count += 1;
                        if root_count > 1 {
                            return Err(xml_error("junk after document element"));
                        }
                        let d = PyDict::new(py);
                        d.set_item(&name, value.unwrap_or_else(|| py.None()))?;
                        root_item = Some(d.unbind());
                    }
                    if streaming {
                        stream_path.pop();
                        depth -= 1;
                    }
                }
            }

            Event::Text(e) => {
                let text = e.unescape().map_err(xml_error)?;
                if let Some(frame) = stack.last_mut() {
                    // quick-xml already suppresses empty-after-trim text events when
                    // trim_text(true) is set (strip_whitespace mode), so most whitespace
                    // never reaches here.  Push any remaining non-empty chunk.
                    if !text.is_empty() {
                        frame.data.push(text.into_owned());
                    }
                }
            }

            Event::CData(e) => {
                let text = std::str::from_utf8(e.as_ref())
                    .map_err(|_| xml_error("invalid UTF-8 in CDATA"))?
                    .to_string();
                if let Some(frame) = stack.last_mut() {
                    frame.data.push(text);
                }
            }

            Event::Empty(ref e) => {
                let name = decode_name(e.name().as_ref())?;
                // An empty element is conceptually at depth+1 relative to current depth
                let effective_depth = depth + 1;

                let (item, path_raw_attrs): (Option<Py<PyDict>>, Option<Py<PyDict>>) =
                    if streaming {
                        let raw = build_raw_attrs_dict(py, e.attributes(), &reader)?;
                        let prefixed = if xml_attribs {
                            build_attrs_dict(py, e.attributes(), attr_prefix, &reader, &mut key_cache, &mut value_cache, intern_values)?
                        } else {
                            None
                        };
                        (prefixed, raw)
                    } else if xml_attribs {
                        (build_attrs_dict(py, e.attributes(), attr_prefix, &reader, &mut key_cache, &mut value_cache, intern_values)?, None)
                    } else {
                        for attr in e.attributes() {
                            attr.map_err(xml_error)?;
                        }
                        (None, None)
                    };

                let value: Option<PyObject> = item.map(|d| d.bind(py).clone().into());

                if streaming && effective_depth == item_depth {
                    // Temporarily push to path, fire callback, then pop
                    stream_path.push((name.clone(), path_raw_attrs));
                    let path_list = build_callback_path(py, &stream_path)?;
                    stream_path.pop();

                    if let Some(ref cb) = item_callback {
                        let item_val: PyObject = value.unwrap_or_else(|| py.None());
                        let result = cb.bind(py).call1((path_list, item_val))?;
                        if !result.is_truthy()? {
                            return Err(crate::ParsingInterrupted::new_err(""));
                        }
                    }
                    // Don't attach to parent
                } else {
                    if let Some(parent) = stack.last_mut() {
                        let parent_item = parent.item.take();
                        let new_item =
                            push_data(py, parent_item, &name, value, &force_list, &mut key_cache)?;
                        parent.item = Some(new_item);
                    } else {
                        root_count += 1;
                        if root_count > 1 {
                            return Err(xml_error("junk after document element"));
                        }
                        let d = PyDict::new(py);
                        d.set_item(&name, value.unwrap_or_else(|| py.None()))?;
                        root_item = Some(d.unbind());
                    }
                }
            }

            Event::DocType(e) => {
                if disable_entities {
                    let content = std::str::from_utf8(e.as_ref()).unwrap_or("");
                    // Case-insensitive check for ENTITY declarations
                    if content.to_ascii_uppercase().contains("<!ENTITY") {
                        return Err(pyo3::exceptions::PyValueError::new_err(
                            "entities are disabled",
                        ));
                    }
                }
            }

            Event::Eof => break,

            // PI, Comment, Decl — ignored
            _ => {}
        }
    }

    // Unclosed tags = malformed document
    if !stack.is_empty() {
        return Err(xml_error("unclosed tag"));
    }

    // Streaming mode always returns None
    if streaming {
        return Ok(py.None());
    }

    // Empty document (no root element)
    if root_count == 0 {
        return Err(xml_error("no element found"));
    }

    Ok(match root_item {
        Some(d) => d.bind(py).clone().into(),
        None => py.None(),
    })
}
