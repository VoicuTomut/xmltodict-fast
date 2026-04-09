import re

try:
    from ._xmltodict_rs import parse as _rs_parse, unparse as _rs_unparse, ParsingInterrupted
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False

from ._pure import parse as _py_parse, unparse as _py_unparse
if not _RUST_AVAILABLE:
    from ._pure import ParsingInterrupted

_BACKEND = "rust" if _RUST_AVAILABLE else "python"

# Matches the encoding= in an XML declaration so we can normalise it for Rust.
_XML_DECL_ENC_RE = re.compile(
    rb"""(<\?xml[^?]*encoding\s*=\s*)(?P<q>['"]) [^'"]* (?P=q)""",
    re.VERBOSE,
)


def _normalise_encoding_decl(xml_bytes: bytes) -> bytes:
    """Replace the encoding declaration with utf-8 (bytes are already UTF-8)."""
    return _XML_DECL_ENC_RE.sub(rb'\1"utf-8"', xml_bytes, count=1)


# ── parse routing ─────────────────────────────────────────────────────────────

def _use_rust_parse(xml_input, kwargs: dict) -> bool:
    if not _RUST_AVAILABLE:
        return False
    if not isinstance(xml_input, (str, bytes, bytearray)):
        return False
    if kwargs.get("process_namespaces", False):
        return False
    if kwargs.get("process_comments", False):
        return False
    if kwargs.get("disable_entities", True) is False:
        return False
    if kwargs.get("dict_constructor", dict) is not dict:
        return False
    if kwargs.get("postprocessor", None) is not None:
        return False
    if kwargs.get("expat", None) is not None:
        return False
    force_list = kwargs.get("force_list", None)
    if callable(force_list):
        return False
    if isinstance(force_list, bool) and force_list:
        return False
    force_cdata = kwargs.get("force_cdata", False)
    if callable(force_cdata):
        return False
    if not isinstance(force_cdata, bool):
        return False
    return True


def parse(xml_input, encoding=None, expat=None, process_namespaces=False,
          namespace_separator=':', disable_entities=True, process_comments=False,
          **kwargs):
    routing = dict(
        process_namespaces=process_namespaces,
        process_comments=process_comments,
        disable_entities=disable_entities,
        expat=expat,
        **kwargs,
    )

    # Materialise file-like objects into bytes so they can be routed to the
    # Rust backend (which requires a contiguous buffer).  Generators are left
    # as-is — they are processed incrementally by the Python SAX path.
    if hasattr(xml_input, 'read'):
        xml_input = xml_input.read()
        if isinstance(xml_input, str):
            xml_input = xml_input.encode(encoding or 'utf-8')

    if _use_rust_parse(xml_input, routing):
        if isinstance(xml_input, str):
            raw = xml_input.encode('utf-8')
            raw = _normalise_encoding_decl(raw)
        else:
            raw = bytes(xml_input)

        rust_kwargs = {}
        for k in ("xml_attribs", "attr_prefix", "cdata_key", "force_cdata",
                  "cdata_separator", "strip_whitespace", "item_depth", "item_callback"):
            if k in kwargs:
                rust_kwargs[k] = kwargs[k]
        fl = kwargs.get("force_list", None)
        if fl and not isinstance(fl, bool):
            rust_kwargs["force_list"] = list(fl)
        rust_kwargs["disable_entities"] = disable_entities
        try:
            return _rs_parse(raw, **rust_kwargs)
        except ValueError as exc:
            msg = str(exc)
            if "entities are disabled" in msg:
                raise
            from xml.parsers.expat import ExpatError
            raise ExpatError(msg) from exc

    from xml.parsers import expat as _default_expat
    return _py_parse(
        xml_input,
        encoding=encoding,
        expat=expat if expat is not None else _default_expat,
        process_namespaces=process_namespaces,
        namespace_separator=namespace_separator,
        disable_entities=disable_entities,
        process_comments=process_comments,
        **kwargs,
    )


# ── unparse routing ───────────────────────────────────────────────────────────

def _use_rust_unparse(kwargs: dict) -> bool:
    if not _RUST_AVAILABLE:
        return False
    # Features that require the Python path
    if kwargs.get("output", None) is not None:
        return False
    if kwargs.get("preprocessor", None) is not None:
        return False
    if kwargs.get("namespaces", None) is not None:
        return False
    if kwargs.get("expand_iter", None) is not None:
        return False
    # Non-default bytes_errors requires Python's codecs machinery
    if kwargs.get("bytes_errors", "replace") != "replace":
        return False
    # Non-UTF-8 encoding + bytes values in the dict would decode incorrectly;
    # route to Python for any non-UTF-8 encoding to be safe.
    enc = kwargs.get("encoding", "utf-8")
    if enc.lower().replace("-", "").replace("_", "") not in ("utf8",):
        return False
    return True


def unparse(input_dict, output=None, encoding='utf-8', full_document=True,
            short_empty_elements=False, comment_key='#comment', **kwargs):
    routing = dict(
        output=output,
        encoding=encoding,
        **kwargs,
    )

    if _use_rust_unparse(routing) and isinstance(input_dict, dict):
        rust_kwargs = {}
        for k in ("attr_prefix", "cdata_key", "pretty", "newl", "indent"):
            if k in kwargs:
                rust_kwargs[k] = kwargs[k]
        return _rs_unparse(
            input_dict,
            encoding=encoding,
            full_document=full_document,
            short_empty_elements=short_empty_elements,
            comment_key=comment_key,
            **rust_kwargs,
        )

    return _py_unparse(
        input_dict,
        output=output,
        encoding=encoding,
        full_document=full_document,
        short_empty_elements=short_empty_elements,
        comment_key=comment_key,
        **kwargs,
    )


__all__ = ["parse", "unparse", "ParsingInterrupted"]
