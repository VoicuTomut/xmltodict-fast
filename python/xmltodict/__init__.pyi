"""
Type stubs for xmltodict.

parse() returns Optional[dict] — None when item_depth > 0 (streaming mode).
unparse() returns Optional[str] — None when output= is provided.
"""

from typing import Any, Callable, IO, Iterator, Optional, Type, Union

# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class ParsingInterrupted(Exception): ...

# ---------------------------------------------------------------------------
# parse()
# ---------------------------------------------------------------------------

# Postprocessor receives (path, key, value) and returns (key, value) or None.
# Returning None drops the key from the result dict.
_Postprocessor = Callable[[list, str, Any], Optional[tuple[str, Any]]]

# force_list / force_cdata accept:
#   bool  — apply to every key
#   tuple — apply only to keys in the tuple
#   callable — (path, key, value) -> bool
_ForceArg = Union[bool, tuple, Callable[[list, str, Any], bool], None]

def parse(
    xml_input: Union[str, bytes, IO[bytes], Iterator[Union[str, bytes]]],
    encoding: Optional[str] = None,
    expat: Any = ...,
    process_namespaces: bool = False,
    namespace_separator: str = ":",
    disable_entities: bool = True,
    process_comments: bool = False,
    # _DictSAXHandler kwargs
    xml_attribs: bool = True,
    attr_prefix: str = "@",
    cdata_key: str = "#text",
    force_cdata: _ForceArg = False,
    cdata_separator: str = "",
    postprocessor: Optional[_Postprocessor] = None,
    dict_constructor: Type[dict] = ...,
    strip_whitespace: bool = True,
    namespaces: Optional[dict[str, Optional[str]]] = None,
    force_list: _ForceArg = None,
    comment_key: str = "#comment",
    item_depth: int = 0,
    item_callback: Callable[[list, Any], bool] = ...,
) -> Optional[dict[str, Any]]:
    """Parse XML input and return a dict, or None in streaming mode.

    <contract>
      xml_input must be a str, bytes, binary file-like object, or a generator
      of str/bytes chunks. StringIO is NOT supported (raises TypeError).
      When item_depth > 0, returns None; items are passed to item_callback.
      When item_callback returns falsy, raises ParsingInterrupted.
      disable_entities=True (default) rejects all entity declarations — do not
      change the default; it is a security guard against XML bomb attacks.
    </contract>

    <returns>
      dict[str, Any] — nested dict mirroring the XML structure, or
      None           — when item_depth > 0 (streaming mode)
    </returns>

    <raises>
      xml.parsers.expat.ExpatError — malformed XML, null bytes, encoding errors
      TypeError                    — xml_input is None or not a supported type
      ParsingInterrupted           — item_callback returned a falsy value
      ValueError                   — entity declaration found with disable_entities=True
    </raises>

    <example>
      >>> parse('<a href="x"><b>1</b><b>2</b></a>')
      {'a': {'@href': 'x', 'b': ['1', '2']}}
    </example>
    """
    ...

# ---------------------------------------------------------------------------
# unparse()
# ---------------------------------------------------------------------------

# Preprocessor receives (key, value) and returns (key, value) or None.
# Returning None drops the element from the output.
_Preprocessor = Callable[[str, Any], Optional[tuple[str, Any]]]

def unparse(
    input_dict: dict[str, Any],
    output: Optional[IO[str]] = None,
    encoding: str = "utf-8",
    full_document: bool = True,
    short_empty_elements: bool = False,
    comment_key: str = "#comment",
    # _emit kwargs
    attr_prefix: str = "@",
    cdata_key: str = "#text",
    preprocessor: Optional[_Preprocessor] = None,
    pretty: bool = False,
    newl: str = "\n",
    indent: Union[str, int] = "\t",
    namespace_separator: str = ":",
    namespaces: Optional[dict[str, str]] = None,
    expand_iter: Optional[str] = None,
    bytes_errors: str = "replace",
) -> Optional[str]:
    """Emit an XML document from a dict (reverse of parse).

    <contract>
      input_dict must be a plain dict. None, list, str, and other types raise
      AttributeError today (known issue — see KNOWN_ISSUES.md #3).
      full_document=True requires exactly one non-comment root key; raises
      ValueError otherwise.
      Empty lists as values are silently skipped (no element emitted).
      bytes_errors must be a valid codecs error handler name; raises ValueError
      if not recognized.
    </contract>

    <returns>
      str           — the XML document as a string, when output=None (default)
      None          — when output= is provided; the XML is written there instead
    </returns>

    <raises>
      ValueError    — no root, multiple roots, invalid element/attribute name,
                      circular structure (RecursionError — see KNOWN_ISSUES.md #5),
                      invalid bytes_errors handler, invalid comment text
      AttributeError — input_dict is not a dict (known issue — KNOWN_ISSUES.md #4)
    </raises>

    <example>
      >>> unparse({'a': {'@href': 'x', 'b': ['1', '2']}})
      '<?xml version="1.0" encoding="utf-8"?>\\n<a href="x"><b>1</b><b>2</b></a>'
    </example>
    """
    ...
