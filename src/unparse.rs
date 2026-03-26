use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyByteArray, PyDict, PyList, PyMemoryView, PyString};

// ── XML escape helpers ────────────────────────────────────────────────────────

/// Escape text content: & < >
fn escape_text(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 8);
    for ch in s.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            c => out.push(c),
        }
    }
    out
}

/// Produce a quoted attribute value matching Python's quoteattr logic.
fn quote_attr(value: &str) -> String {
    let esc = escape_text(value);
    if esc.contains('"') {
        if esc.contains('\'') {
            format!("\"{}\"", esc.replace('"', "&quot;"))
        } else {
            format!("'{}'", esc)
        }
    } else {
        format!("\"{}\"", esc)
    }
}

// ── validation (mirrors Python's _validate_name / _validate_comment) ──────────

fn validate_name(value: &str, kind: &str) -> PyResult<()> {
    // Note: empty string passes intentionally (known bug, documented in KNOWN_ISSUES.md)
    if value.starts_with('?') || value.starts_with('!') {
        return Err(PyValueError::new_err(format!(
            "Invalid {kind} name: cannot start with \"?\" or \"!\""
        )));
    }
    if value.contains('<') || value.contains('>') {
        return Err(PyValueError::new_err(format!(
            "Invalid {kind} name: \"<\" or \">\" not allowed"
        )));
    }
    if value.contains('/') {
        return Err(PyValueError::new_err(format!(
            "Invalid {kind} name: \"/\" not allowed"
        )));
    }
    if value.contains('"') || value.contains('\'') {
        return Err(PyValueError::new_err(format!(
            "Invalid {kind} name: quotes not allowed"
        )));
    }
    if value.contains('=') {
        return Err(PyValueError::new_err(format!(
            "Invalid {kind} name: \"=\" not allowed"
        )));
    }
    if value.chars().any(|c| c.is_whitespace()) {
        return Err(PyValueError::new_err(format!(
            "Invalid {kind} name: whitespace not allowed"
        )));
    }
    Ok(())
}

fn validate_comment_text(text: &str) -> PyResult<()> {
    if text.contains("--") {
        return Err(PyValueError::new_err("Comment text cannot contain '--'"));
    }
    if text.ends_with('-') {
        return Err(PyValueError::new_err("Comment text cannot end with '-'"));
    }
    Ok(())
}

// ── value → string conversion (mirrors _convert_value_to_string) ─────────────

/// Convert a Python value to String. Bool must be checked before int/str.
fn py_to_str(py: Python<'_>, v: &Bound<'_, PyAny>) -> PyResult<String> {
    // bool is a subclass of int — check it first
    if v.is_instance_of::<PyBool>() {
        let b: bool = v.extract()?;
        return Ok(if b { "true".to_string() } else { "false".to_string() });
    }
    if let Ok(s) = v.downcast::<PyString>() {
        return Ok(s.to_str()?.to_string());
    }
    if let Ok(b) = v.downcast::<PyBytes>() {
        return Ok(String::from_utf8_lossy(b.as_bytes()).into_owned());
    }
    if let Ok(b) = v.downcast::<PyByteArray>() {
        return Ok(String::from_utf8_lossy(&b.to_vec()).into_owned());
    }
    // memoryview: call .tobytes() to get raw bytes, then decode as UTF-8
    if v.is_instance_of::<PyMemoryView>() {
        let raw = v.call_method0(pyo3::intern!(py, "tobytes"))?;
        let bytes = raw.downcast::<PyBytes>()?;
        return Ok(String::from_utf8_lossy(bytes.as_bytes()).into_owned());
    }
    // fall through: call Python str()
    let _ = py;
    Ok(v.str()?.to_str()?.to_string())
}

// ── options ───────────────────────────────────────────────────────────────────

struct Opts {
    attr_prefix: String,
    cdata_key: String,
    comment_key: String,
    short_empty_elements: bool,
    pretty: bool,
    newl: String,
    indent: String,
}

impl Opts {
    fn ind(&self, buf: &mut String, depth: usize) {
        if self.pretty {
            for _ in 0..depth {
                buf.push_str(&self.indent);
            }
        }
    }
    fn nl(&self, buf: &mut String) {
        if self.pretty {
            buf.push_str(&self.newl);
        }
    }
}

// ── emit helpers ──────────────────────────────────────────────────────────────

fn emit_comment(
    buf: &mut String,
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
    depth: usize,
    opts: &Opts,
) -> PyResult<()> {
    // Value is either a single item or a list of items
    let items: Vec<Bound<'_, PyAny>> = if let Ok(lst) = value.downcast::<PyList>() {
        lst.iter().collect()
    } else {
        vec![value.clone()]
    };

    for item in items {
        if item.is_none() {
            continue;
        }
        let text = py_to_str(py, &item)?;
        if text.is_empty() {
            continue;
        }
        validate_comment_text(&text)?;
        opts.ind(buf, depth);
        buf.push_str("<!--");
        buf.push_str(&escape_text(&text));
        buf.push_str("-->");
        opts.nl(buf);
    }
    Ok(())
}

/// Separate a dict into cdata, attrs, and children.
fn process_dict(
    py: Python<'_>,
    d: &Bound<'_, PyDict>,
    opts: &Opts,
    cdata: &mut Option<String>,
    attrs: &mut Vec<(String, String)>,    // (attr_name, quoted_value)
    children: &mut Vec<(String, Py<PyAny>)>,
) -> PyResult<()> {
    for (dk, dv) in d.iter() {
        // Key → string
        let dk_str: String = if let Ok(s) = dk.downcast::<PyString>() {
            s.to_str()?.to_string()
        } else {
            // Non-string key: convert via str(), then validate (may raise ValueError)
            dk.str()?.to_str()?.to_string()
        };

        // cdata_key ('#text')
        if dk_str == opts.cdata_key {
            *cdata = if dv.is_none() {
                None
            } else {
                Some(py_to_str(py, &dv)?)
            };
            continue;
        }

        // attr_prefix ('@…')
        if let Some(attr_name) = dk_str.strip_prefix(opts.attr_prefix.as_str()) {
            // Special case: @xmlns with a dict value → xmlns:prefix= attributes
            if attr_name == "xmlns" && dv.is_instance_of::<PyDict>() {
                let xmlns_dict = dv.downcast::<PyDict>()?;
                for (k, v) in xmlns_dict.iter() {
                    let prefix = k.str()?.to_str()?.to_string();
                    validate_name(&prefix, "attribute")?;
                    let attr = if prefix.is_empty() {
                        "xmlns".to_string()
                    } else {
                        format!("xmlns:{prefix}")
                    };
                    let val = if v.is_none() {
                        String::new()
                    } else {
                        py_to_str(py, &v)?
                    };
                    attrs.push((attr, quote_attr(&val)));
                }
                continue;
            }

            validate_name(attr_name, "attribute")?;
            let val = if dv.is_none() {
                String::new()
            } else {
                py_to_str(py, &dv)?
            };
            attrs.push((attr_name.to_string(), quote_attr(&val)));
            continue;
        }

        // Child element — skip empty lists (preserves Python _emit behaviour)
        let is_empty_list = dv
            .downcast::<PyList>()
            .map(|l| l.is_empty())
            .unwrap_or(false);
        if !is_empty_list {
            children.push((dk_str, dv.unbind()));
        }
    }
    Ok(())
}

/// Emit one (possibly normalised) value as an XML element.
fn emit_single_value(
    buf: &mut String,
    py: Python<'_>,
    key: &str,
    v: &Bound<'_, PyAny>,
    depth: usize,
    opts: &Opts,
) -> PyResult<()> {
    let mut cdata: Option<String> = None;
    let mut attrs: Vec<(String, String)> = Vec::new();
    let mut children: Vec<(String, Py<PyAny>)> = Vec::new();

    if v.is_none() {
        // empty element
    } else if let Ok(d) = v.downcast::<PyDict>() {
        process_dict(py, d, opts, &mut cdata, &mut attrs, &mut children)?;
    } else if let Ok(s) = v.downcast::<PyString>() {
        cdata = Some(s.to_str()?.to_string());
    } else {
        // Non-string scalar (int, float, bool, bytes, …) → convert to string → cdata
        cdata = Some(py_to_str(py, v)?);
    }

    let has_children = !children.is_empty();
    let has_cdata = cdata.is_some();
    let has_content = has_children || has_cdata;

    // ── start tag ─────────────────────────────────────────────────────────────
    opts.ind(buf, depth);
    buf.push('<');
    buf.push_str(key);
    for (attr_name, attr_quoted) in &attrs {
        buf.push(' ');
        buf.push_str(attr_name);
        buf.push('=');
        buf.push_str(attr_quoted);
    }

    // Self-closing for empty elements (same logic as Python)
    if !has_content && opts.short_empty_elements {
        buf.push_str("/>");
        if opts.pretty && depth > 0 {
            buf.push_str(&opts.newl);
        }
        return Ok(());
    }

    buf.push('>');
    if opts.pretty && has_children {
        buf.push_str(&opts.newl);
    }

    // ── children ──────────────────────────────────────────────────────────────
    for (child_key, child_py) in &children {
        let child_val = child_py.bind(py);
        emit_key(buf, py, child_key, child_val, depth + 1, opts, false)?;
    }

    // ── cdata ─────────────────────────────────────────────────────────────────
    if let Some(ref text) = cdata {
        buf.push_str(&escape_text(text));
    }

    // ── end tag ───────────────────────────────────────────────────────────────
    // Indent before end tag only when pretty AND has children
    if opts.pretty && has_children {
        opts.ind(buf, depth);
    }
    buf.push_str("</");
    buf.push_str(key);
    buf.push('>');
    if opts.pretty && depth > 0 {
        buf.push_str(&opts.newl);
    }

    Ok(())
}

const MAX_DEPTH: usize = 500; // match Python's default recursion limit

/// Emit key/value pair; handles comment_key, list iteration, multiple-root guard.
fn emit_key(
    buf: &mut String,
    py: Python<'_>,
    key: &str,
    value: &Bound<'_, PyAny>,
    depth: usize,
    opts: &Opts,
    full_document: bool,
) -> PyResult<()> {
    if depth > MAX_DEPTH {
        return Err(pyo3::exceptions::PyRecursionError::new_err(
            "maximum recursion depth exceeded",
        ));
    }
    if key == opts.comment_key {
        return emit_comment(buf, py, value, depth, opts);
    }

    validate_name(key, "element")?;

    // Determine whether to iterate value as a sequence.
    // Mirrors Python: wrap in [v] unless it has __iter__ and is NOT str/bytes/bytearray/dict.
    // Mirrors Python: wrap in [v] if not iterable OR if is str/bytes/bytearray/memoryview/dict
    let is_scalar = value.is_instance_of::<PyString>()
        || value.is_instance_of::<PyBytes>()
        || value.is_instance_of::<PyByteArray>()
        || value.is_instance_of::<PyMemoryView>()
        || value.is_instance_of::<PyDict>()
        || value.is_none();

    if is_scalar {
        emit_single_value(buf, py, key, value, depth, opts)?;
    } else {
        // Try to iterate (handles list, tuple, generator, etc.)
        match value.try_iter() {
            Ok(py_iter) => {
                let mut index: usize = 0;
                for item_result in py_iter {
                    let item = item_result?;
                    if full_document && depth == 0 && index > 0 {
                        return Err(PyValueError::new_err("document with multiple roots"));
                    }
                    emit_single_value(buf, py, key, &item, depth, opts)?;
                    index += 1;
                }
            }
            Err(_) => {
                // Not iterable → treat as scalar
                emit_single_value(buf, py, key, value, depth, opts)?;
            }
        }
    }
    Ok(())
}

// ── public entry point ────────────────────────────────────────────────────────

#[pyfunction]
#[pyo3(signature = (
    input_dict,
    encoding = "utf-8",
    full_document = true,
    short_empty_elements = false,
    comment_key = "#comment",
    attr_prefix = "@",
    cdata_key = "#text",
    pretty = false,
    newl = "\n",
    indent = None,
    bytes_errors = "replace",
))]
pub fn unparse(
    py: Python<'_>,
    input_dict: &Bound<'_, PyDict>,
    encoding: &str,
    full_document: bool,
    short_empty_elements: bool,
    comment_key: &str,
    attr_prefix: &str,
    cdata_key: &str,
    pretty: bool,
    newl: &str,
    indent: Option<&Bound<'_, PyAny>>,
    bytes_errors: &str,
) -> PyResult<String> {
    // bytes_errors is accepted but only 'replace' (default) is handled in Rust.
    // Non-default values are routed to Python before this function is called.
    let _ = bytes_errors;

    // Resolve indent: None → '\t', int n → ' '*n, str → as-is
    let indent_str: String = match indent {
        None => "\t".to_string(),
        Some(v) => {
            if let Ok(n) = v.extract::<usize>() {
                " ".repeat(n)
            } else {
                v.extract::<String>()?
            }
        }
    };

    let opts = Opts {
        attr_prefix: attr_prefix.to_string(),
        cdata_key: cdata_key.to_string(),
        comment_key: comment_key.to_string(),
        short_empty_elements,
        pretty,
        newl: newl.to_string(),
        indent: indent_str,
    };

    let mut buf = String::with_capacity(512 + input_dict.len() * 64);

    // XML declaration
    if full_document {
        buf.push_str(&format!(
            "<?xml version=\"1.0\" encoding=\"{encoding}\"?>\n"
        ));
    }

    let mut seen_root = false;

    for (key, value) in input_dict.iter() {
        let key_str: String = if let Ok(s) = key.downcast::<PyString>() {
            s.to_str()?.to_string()
        } else {
            key.str()?.to_str()?.to_string()
        };

        if key_str != comment_key && full_document && seen_root {
            return Err(PyValueError::new_err("Document must have exactly one root."));
        }

        emit_key(&mut buf, py, &key_str, &value, 0, &opts, full_document)?;

        if key_str != comment_key {
            seen_root = true;
        }
    }

    if full_document && !seen_root {
        return Err(PyValueError::new_err("Document must have exactly one root."));
    }

    Ok(buf)
}
