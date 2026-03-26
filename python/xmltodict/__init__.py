import re
from inspect import isgenerator

try:
    from ._xmltodict_rs import parse as _rs_parse
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False

from ._pure import parse as _py_parse, unparse, ParsingInterrupted

_BACKEND = "rust" if _RUST_AVAILABLE else "python"

# Matches the encoding= in an XML declaration so we can normalise it for Rust.
_XML_DECL_ENC_RE = re.compile(
    rb"""(<\?xml[^?]*encoding\s*=\s*)(?P<q>['"]) [^'""]* (?P=q)""",
    re.VERBOSE,
)


def _normalise_encoding_decl(xml_bytes: bytes) -> bytes:
    """Replace the encoding declaration with utf-8 (bytes are already UTF-8)."""
    return _XML_DECL_ENC_RE.sub(rb'\1"utf-8"', xml_bytes, count=1)


def _use_rust(xml_input, kwargs: dict) -> bool:
    """Return True if this call can go through the Rust fast-path."""
    if not _RUST_AVAILABLE:
        return False
    # Only str/bytes inputs are supported by the Rust path
    if not isinstance(xml_input, (str, bytes, bytearray)):
        return False
    # Features that require the Python path
    if kwargs.get("process_namespaces", False):
        return False
    if kwargs.get("process_comments", False):
        return False
    if kwargs.get("item_depth", 0) != 0:
        return False
    if kwargs.get("disable_entities", True) is False:
        return False
    if kwargs.get("dict_constructor", dict) is not dict:
        return False
    if kwargs.get("postprocessor", None) is not None:
        return False
    if kwargs.get("expat", None) is not None:
        return False
    # force_list: callable or bool=True → Python
    force_list = kwargs.get("force_list", None)
    if callable(force_list):
        return False
    if isinstance(force_list, bool) and force_list:
        return False
    # force_cdata: only bool is handled by Rust; tuple/callable → Python
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

    if _use_rust(xml_input, routing):
        # Convert str → UTF-8 bytes, normalising any encoding declaration so
        # that quick-xml doesn't try to re-decode already-decoded Unicode.
        if isinstance(xml_input, str):
            raw = xml_input.encode('utf-8')
            raw = _normalise_encoding_decl(raw)
        else:
            raw = bytes(xml_input)

        rust_kwargs = {}
        for k in ("xml_attribs", "attr_prefix", "cdata_key", "force_cdata",
                  "cdata_separator", "strip_whitespace"):
            if k in kwargs:
                rust_kwargs[k] = kwargs[k]
        # force_list=False is equivalent to None; only pass a tuple/list
        fl = kwargs.get("force_list", None)
        if fl and not isinstance(fl, bool):
            rust_kwargs["force_list"] = list(fl)
        rust_kwargs["disable_entities"] = disable_entities
        try:
            return _rs_parse(raw, **rust_kwargs)
        except ValueError as exc:
            msg = str(exc)
            # "entities are disabled" must stay as ValueError — tests match it.
            if "entities are disabled" in msg:
                raise
            # All other parse errors → ExpatError to match the Python backend.
            from xml.parsers.expat import ExpatError
            raise ExpatError(msg) from exc

    # Python fallback (PyPy, streaming, callables, custom expat, etc.)
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


__all__ = ["parse", "unparse", "ParsingInterrupted"]
