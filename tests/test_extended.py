"""Extended crash-prevention and coverage tests.

These tests complement the existing test_xmltodict.py and test_dicttoxml.py suites.
They cover edge-cases, crash paths, and combinations not tested upstream.
All existing tests remain untouched.
"""
import collections
import re
from io import BytesIO, StringIO

import pytest
from xml.parsers.expat import ExpatError

from xmltodict import parse, unparse, ParsingInterrupted

_HEADER_RE = re.compile(r"^[^\n]*\n")


def _strip(fullxml):
    return _HEADER_RE.sub("", fullxml)


# ---------------------------------------------------------------------------
# parse – malformed / invalid input
# ---------------------------------------------------------------------------

class TestParseInvalidInput:
    def test_malformed_xml_raises(self):
        with pytest.raises(ExpatError):
            parse("<a><b></a>")

    def test_unclosed_tag_raises(self):
        with pytest.raises(ExpatError):
            parse("<a>")

    def test_empty_string_raises(self):
        with pytest.raises(ExpatError):
            parse("")

    def test_empty_bytes_raises(self):
        with pytest.raises(ExpatError):
            parse(b"")

    def test_non_xml_string_raises(self):
        with pytest.raises(ExpatError):
            parse("not xml at all")

    def test_multiple_roots_raises(self):
        with pytest.raises(ExpatError):
            parse("<a/><b/>")


# ---------------------------------------------------------------------------
# parse – input types
# ---------------------------------------------------------------------------

class TestParseInputTypes:
    def test_bytes_input(self):
        assert parse(b"<a>hello</a>") == {"a": "hello"}

    def test_bytesio_input(self):
        assert parse(BytesIO(b"<a>hello</a>")) == {"a": "hello"}

    def test_stringio_input(self):
        # StringIO.read() returns str, which the routing layer encodes to bytes
        assert parse(StringIO("<a>hello</a>")) == {"a": "hello"}

    def test_generator_of_bytes_chunks(self):
        chunks = (c.encode() for c in ["<a>", "hel", "lo", "</a>"])
        assert parse(chunks) == {"a": "hello"}

    def test_generator_of_string_chunks(self):
        chunks = (c for c in ["<a>", "hel", "lo", "</a>"])
        assert parse(chunks) == {"a": "hello"}


# ---------------------------------------------------------------------------
# parse – file objects and generators with streaming
# ---------------------------------------------------------------------------

class TestParseFileObjectStreaming:
    def test_bytesio_streaming_with_callback(self):
        xml = b"<root><item>1</item><item>2</item><item>3</item></root>"
        items = []
        def cb(path, item):
            items.append(item)
            return True
        parse(BytesIO(xml), item_depth=2, item_callback=cb)
        assert items == ["1", "2", "3"]

    def test_stringio_streaming_with_callback(self):
        xml = "<root><item>a</item><item>b</item></root>"
        items = []
        def cb(path, item):
            items.append(item)
            return True
        parse(StringIO(xml), item_depth=2, item_callback=cb)
        assert items == ["a", "b"]

    def test_bytesio_with_force_list(self):
        xml = b"<root><item>1</item></root>"
        result = parse(BytesIO(xml), force_list=("item",))
        assert result == {"root": {"item": ["1"]}}


# ---------------------------------------------------------------------------
# parse – dict_constructor
# ---------------------------------------------------------------------------

class TestParseDictConstructor:
    def test_ordered_dict(self):
        xml = "<root><a>1</a><b>2</b><c>3</c></root>"
        result = parse(xml, dict_constructor=collections.OrderedDict)
        assert isinstance(result, collections.OrderedDict)
        assert list(result["root"].keys()) == ["a", "b", "c"]

    def test_custom_dict_subclass(self):
        class MyDict(dict):
            pass

        result = parse("<a><b>1</b></a>", dict_constructor=MyDict)
        assert isinstance(result, MyDict)

    def test_default_is_plain_dict(self):
        result = parse("<a>1</a>")
        assert type(result) is dict


# ---------------------------------------------------------------------------
# parse – force_list boolean
# ---------------------------------------------------------------------------

class TestParseForceListBool:
    def test_force_list_true_wraps_every_element(self):
        # force_list=True applies to every key including the root child,
        # so <a> itself is wrapped: {"a": [{"b": ["1"]}]}
        result = parse("<a><b>1</b></a>", force_list=True)
        assert result == {"a": [{"b": ["1"]}]}

    def test_force_list_false_is_default(self):
        result = parse("<a><b>1</b></a>", force_list=False)
        assert result == {"a": {"b": "1"}}

    def test_force_list_true_already_multiple(self):
        # <a> is still wrapped in a list; <b> was already a list
        result = parse("<a><b>1</b><b>2</b></a>", force_list=True)
        assert result == {"a": [{"b": ["1", "2"]}]}


# ---------------------------------------------------------------------------
# parse – cdata_separator
# ---------------------------------------------------------------------------

class TestParseCdataSeparator:
    def test_newline_separator(self):
        result = parse("<a>foo<b/>bar</a>", cdata_separator="\n")
        assert result == {"a": {"b": None, "#text": "foo\nbar"}}

    def test_pipe_separator(self):
        result = parse("<a>x<b/>y<c/>z</a>", cdata_separator="|")
        assert result["a"]["#text"] == "x|y|z"

    def test_empty_separator_is_default(self):
        result = parse("<a>foo<b/>bar</a>")
        assert result["a"]["#text"] == "foobar"


# ---------------------------------------------------------------------------
# parse – strip_whitespace
# ---------------------------------------------------------------------------

class TestParseStripWhitespace:
    def test_strip_whitespace_default(self):
        assert parse("<a>  hello  </a>") == {"a": "hello"}

    def test_strip_whitespace_false_preserves(self):
        assert parse("<a>  hello  </a>", strip_whitespace=False) == {"a": "  hello  "}

    def test_strip_whitespace_all_spaces_becomes_none(self):
        assert parse("<a>   </a>") == {"a": None}

    def test_strip_whitespace_false_spaces_only_preserved(self):
        assert parse("<a>   </a>", strip_whitespace=False) == {"a": "   "}


# ---------------------------------------------------------------------------
# parse – comments with strip_whitespace
# ---------------------------------------------------------------------------

class TestParseCommentsWhitespace:
    def test_comment_stripped_by_default(self):
        xml = "<a><!-- note --></a>"
        result = parse(xml, process_comments=True)
        assert result["a"]["#comment"] == "note"

    def test_comment_not_stripped_when_disabled(self):
        xml = "<a><!-- note --></a>"
        result = parse(xml, process_comments=True, strip_whitespace=False)
        assert result["a"]["#comment"] == " note "

    def test_comment_ignored_when_process_comments_false(self):
        xml = "<a><!-- secret --><b>1</b></a>"
        result = parse(xml, process_comments=False)
        assert "#comment" not in result.get("a", {})
        assert result["a"]["b"] == "1"

    def test_multiple_comments_same_element(self):
        xml = "<a><!--c1--><!--c2--><b>1</b></a>"
        result = parse(xml, process_comments=True)
        comments = result["a"]["#comment"]
        assert isinstance(comments, list)
        assert set(comments) == {"c1", "c2"}


# ---------------------------------------------------------------------------
# parse – CDATA sections
# ---------------------------------------------------------------------------

class TestParseCDATA:
    def test_cdata_section_plain(self):
        result = parse("<a><![CDATA[hello world]]></a>")
        assert result == {"a": "hello world"}

    def test_cdata_section_with_special_chars(self):
        result = parse("<a><![CDATA[<b>&amp;</b>]]></a>")
        assert result == {"a": "<b>&amp;</b>"}

    def test_cdata_section_and_text_combined(self):
        result = parse("<a>pre<![CDATA[mid]]>post</a>")
        assert result == {"a": "premidpost"}


# ---------------------------------------------------------------------------
# parse – attribute edge cases
# ---------------------------------------------------------------------------

class TestParseAttributeEdgeCases:
    def test_attribute_empty_value(self):
        assert parse('<a href=""/>') == {"a": {"@href": ""}}

    def test_attribute_with_special_chars(self):
        result = parse('<a href="a&amp;b"/>')
        assert result == {"a": {"@href": "a&b"}}

    def test_multiple_attributes(self):
        result = parse('<a x="1" y="2" z="3"/>')
        assert result["a"]["@x"] == "1"
        assert result["a"]["@y"] == "2"
        assert result["a"]["@z"] == "3"

    def test_custom_attr_prefix_empty_string(self):
        result = parse('<a href="x"/>', attr_prefix="")
        assert result == {"a": {"href": "x"}}


# ---------------------------------------------------------------------------
# parse – streaming edge cases
# ---------------------------------------------------------------------------

class TestParseStreamingEdgeCases:
    def test_depth_one_root_element(self):
        items = []
        def cb(path, item):
            items.append((path, item))
            return True
        parse("<root><a>1</a><b>2</b></root>", item_depth=1, item_callback=cb)
        assert len(items) == 1
        assert items[0][1] == {"a": "1", "b": "2"}

    def test_depth_exceeds_tree_no_callbacks(self):
        called = []
        def cb(path, item):
            called.append(item)
            return True
        result = parse("<a><b>1</b></a>", item_depth=5, item_callback=cb)
        assert called == []
        assert result is None

    def test_streaming_empty_element(self):
        items = []
        def cb(path, item):
            items.append(item)
            return True
        parse("<root><item/></root>", item_depth=2, item_callback=cb)
        assert items == [None]

    def test_streaming_interrupt_raises(self):
        count = [0]
        def cb(path, item):
            count[0] += 1
            return count[0] < 2  # stop after first
        with pytest.raises(ParsingInterrupted):
            parse("<a><b>1</b><b>2</b><b>3</b></a>", item_depth=2, item_callback=cb)
        assert count[0] == 2

    def test_streaming_with_depth_zero_behaves_normal(self):
        # item_depth=0 → normal (non-streaming) mode
        result = parse("<a><b>1</b></a>", item_depth=0)
        assert result == {"a": {"b": "1"}}


# ---------------------------------------------------------------------------
# parse – postprocessor edge cases
# ---------------------------------------------------------------------------

class TestParsePostprocessorEdgeCases:
    def test_postprocessor_receives_path(self):
        paths_seen = []
        def pp(path, key, value):
            paths_seen.append(list(path))
            return key, value
        parse("<a><b>1</b></a>", postprocessor=pp)
        assert any(len(p) > 0 for p in paths_seen)

    def test_postprocessor_can_return_none_to_skip(self):
        def pp(path, key, value):
            if key == "secret":
                return None
            return key, value
        result = parse("<a><secret>x</secret><public>y</public></a>", postprocessor=pp)
        assert "secret" not in result["a"]
        assert result["a"]["public"] == "y"

    def test_postprocessor_can_transform_value(self):
        def pp(path, key, value):
            if isinstance(value, str) and value.isdigit():
                return key, int(value)
            return key, value
        result = parse("<a><n>42</n><s>hello</s></a>", postprocessor=pp)
        assert result["a"]["n"] == 42
        assert result["a"]["s"] == "hello"


# ---------------------------------------------------------------------------
# parse – deeply nested
# ---------------------------------------------------------------------------

class TestParseDeeplyNested:
    def test_deep_nesting(self):
        depth = 50
        xml = "".join(f"<l{i}>" for i in range(depth))
        xml += "value"
        xml += "".join(f"</l{i}>" for i in range(depth - 1, -1, -1))
        result = parse(xml)
        # walk down
        node = result
        for i in range(depth):
            node = node[f"l{i}"]
        assert node == "value"

    def test_wide_element_many_children(self):
        children = "".join(f"<c{i}>v{i}</c{i}>" for i in range(100))
        xml = f"<root>{children}</root>"
        result = parse(xml)
        assert result["root"]["c0"] == "v0"
        assert result["root"]["c99"] == "v99"


# ---------------------------------------------------------------------------
# parse – namespace edge cases
# ---------------------------------------------------------------------------

class TestParseNamespaceEdgeCases:
    def test_process_namespaces_no_declarations(self):
        result = parse("<a>1</a>", process_namespaces=True)
        assert result == {"a": "1"}

    def test_namespace_separator_custom(self):
        xml = '<root xmlns:x="http://x.com/"><x:child>1</x:child></root>'
        result = parse(xml, process_namespaces=True, namespace_separator="|")
        key = "http://x.com/|child"
        assert result["root"][key] == "1"


# ---------------------------------------------------------------------------
# unparse – output file parameter
# ---------------------------------------------------------------------------

class TestUnparseOutputParam:
    def test_output_stringio(self):
        buf = StringIO()
        ret = unparse({"a": "1"}, output=buf)
        assert ret is None  # returns None when output is given
        assert "<a>1</a>" in buf.getvalue()

    def test_output_bytesio(self):
        buf = BytesIO()
        ret = unparse({"a": "hello"}, output=buf, encoding="utf-8")
        assert ret is None
        content = buf.getvalue().decode("utf-8")
        assert "<a>hello</a>" in content


# ---------------------------------------------------------------------------
# unparse – custom keys
# ---------------------------------------------------------------------------

class TestUnparseCustomKeys:
    def test_custom_cdata_key(self):
        obj = {"a": {"_text_": "hello"}}
        result = _strip(unparse(obj, cdata_key="_text_"))
        assert result == "<a>hello</a>"

    def test_custom_attr_prefix(self):
        obj = {"a": {"!href": "x"}}
        result = _strip(unparse(obj, attr_prefix="!"))
        assert result == '<a href="x"></a>'

    def test_custom_comment_key(self):
        obj = {"a": {"__note__": "hi", "b": "1"}}
        result = _strip(unparse(obj, comment_key="__note__", full_document=True))
        assert "<!--hi-->" in result
        assert "<b>1</b>" in result


# ---------------------------------------------------------------------------
# unparse – text escaping
# ---------------------------------------------------------------------------

class TestUnparseTextEscaping:
    def test_ampersand_in_text_escaped(self):
        xml = unparse({"a": "x&y"}, full_document=False)
        assert "&amp;" in xml

    def test_lt_in_text_escaped(self):
        xml = unparse({"a": "x<y"}, full_document=False)
        assert "&lt;" in xml

    def test_gt_in_text_escaped(self):
        xml = unparse({"a": "x>y"}, full_document=False)
        assert "&gt;" in xml

    def test_ampersand_in_attribute_escaped(self):
        xml = unparse({"a": {"@href": "x&y"}}, full_document=False)
        assert "&amp;" in xml

    def test_quotes_in_attribute_valid_xml(self):
        # XMLGenerator may wrap the attribute in single quotes when the value
        # contains double quotes — that is valid XML, no escaping required
        xml = unparse({"a": {"@title": 'say "hi"'}}, full_document=False)
        # Either escaped or wrapped in single-quotes — the round-trip must work
        assert parse(xml)["a"]["@title"] == 'say "hi"'


# ---------------------------------------------------------------------------
# unparse – pretty print edge cases
# ---------------------------------------------------------------------------

class TestUnparsePrettyEdgeCases:
    def test_indent_zero_no_indentation(self):
        obj = {"a": {"b": "1"}}
        xml = unparse(obj, pretty=True, indent=0, newl="\n", full_document=False)
        lines = xml.split("\n")
        # no line should start with spaces
        for line in lines:
            if line:
                assert not line.startswith(" "), f"unexpected indent in: {line!r}"

    def test_newl_empty_string(self):
        obj = {"a": {"b": "1"}}
        xml = unparse(obj, pretty=True, newl="", indent="\t", full_document=False)
        assert "\n" not in xml

    def test_custom_newl_and_indent(self):
        obj = {"a": {"b": "1"}}
        xml = unparse(obj, pretty=True, newl="|", indent=">>", full_document=False)
        assert "|" in xml
        assert ">>" in xml


# ---------------------------------------------------------------------------
# unparse – expand_iter
# ---------------------------------------------------------------------------

class TestUnparseExpandIter:
    def test_expand_iter_with_list_of_lists(self):
        # expand_iter unwraps inner iterables — each inner list becomes
        # a set of <item> children inside one <b> element
        obj = {"a": {"b": [["x", "y"], ["z"]]}}
        xml = _strip(unparse(obj, expand_iter="item"))
        assert xml == "<a><b><item>x</item><item>y</item></b><b><item>z</item></b></a>"

    def test_tuple_as_value_produces_repeated_elements(self):
        # A tuple at the value level is iterated directly: each entry → one <b>
        obj = {"a": {"b": (1, 2, 3)}}
        xml = _strip(unparse(obj))
        assert xml == "<a><b>1</b><b>2</b><b>3</b></a>"

    def test_generator_as_value_produces_repeated_elements(self):
        obj = {"a": {"b": (x for x in [1, 2])}}
        xml = _strip(unparse(obj))
        assert xml == "<a><b>1</b><b>2</b></a>"

    def test_set_as_value_produces_element(self):
        obj = {"a": {"b": {42}}}
        xml = _strip(unparse(obj))
        assert "<b>42</b>" in xml


# ---------------------------------------------------------------------------
# unparse – full_document=False with only comment key
# ---------------------------------------------------------------------------

class TestUnparseCommentOnlyNoDoc:
    def test_comment_only_no_root_no_error(self):
        # full_document=False, only a comment key — no root, no error
        obj = {"#comment": "note"}
        xml = unparse(obj, full_document=False)
        assert "<!--note-->" in xml

    def test_full_document_true_comment_only_raises(self):
        obj = {"#comment": "note"}
        with pytest.raises(ValueError):
            unparse(obj, full_document=True)


# ---------------------------------------------------------------------------
# unparse – deeply nested
# ---------------------------------------------------------------------------

class TestUnparseDeeplyNested:
    def test_deep_roundtrip(self):
        depth = 30
        # Build nested dict
        obj = {"v": "leaf"}
        for i in range(depth - 1, -1, -1):
            obj = {f"l{i}": obj}
        xml = unparse(obj)
        result = parse(xml)
        # Walk back down
        node = result
        for i in range(depth):
            node = node[f"l{i}"]
        assert node == {"v": "leaf"}


# ---------------------------------------------------------------------------
# unparse – numeric / special values
# ---------------------------------------------------------------------------

class TestUnparseSpecialValues:
    def test_float_value(self):
        xml = unparse({"a": 3.14}, full_document=False)
        assert "<a>3.14</a>" == xml

    def test_none_text_produces_empty_element(self):
        xml = _strip(unparse({"a": None}))
        assert xml == "<a></a>"

    def test_list_of_none_produces_empty_elements(self):
        xml = unparse({"a": {"b": [None, None]}}, full_document=False)
        assert xml.count("<b>") == 2

    def test_memoryview_value(self):
        xml = unparse({"a": memoryview(b"hello")}, full_document=False)
        assert "<a>hello</a>" == xml


# ---------------------------------------------------------------------------
# round-trip tests (parse → unparse → parse)
# ---------------------------------------------------------------------------

class TestRoundTrips:
    def test_roundtrip_with_namespaces(self):
        xml = '<root xmlns:x="http://x.com/"><x:item>val</x:item></root>'
        parsed = parse(xml)
        reparsed = parse(unparse(parsed))
        assert reparsed == parsed

    def test_roundtrip_with_list(self):
        obj = {"root": {"item": ["a", "b", "c"]}}
        assert parse(unparse(obj)) == obj

    def test_roundtrip_with_attrs_and_text(self):
        obj = {"root": {"@id": "1", "#text": "hello"}}
        assert parse(unparse(obj)) == obj

    def test_roundtrip_force_cdata(self):
        obj = {"root": {"#text": "data"}}
        xml = unparse(obj)
        assert parse(xml, force_cdata=True) == obj

    def test_roundtrip_unicode(self):
        obj = {"a": "\u9999\u4e16\u754c"}
        assert parse(unparse(obj)) == obj

    def test_roundtrip_empty_attrib_value(self):
        obj = {"a": {"@href": ""}}
        assert parse(unparse(obj)) == obj

    def test_roundtrip_nested_lists(self):
        obj = {"root": {"a": ["1", "2"], "b": ["x", "y"]}}
        assert parse(unparse(obj)) == obj

    def test_roundtrip_with_force_list(self):
        xml = "<root><item>one</item></root>"
        parsed = parse(xml, force_list=("item",))
        assert isinstance(parsed["root"]["item"], list)
        assert parse(unparse(parsed), force_list=("item",)) == parsed


# ---------------------------------------------------------------------------
# Crash vectors – confirmed by live probing
# Each test documents an exact failure mode so future regressions are caught.
# ---------------------------------------------------------------------------

class TestParseCrashVectors:
    def test_none_input_raises_typeerror(self):
        # parser.Parse(None, True) → TypeError: a bytes-like object is required
        with pytest.raises(TypeError):
            parse(None)

    def test_null_byte_in_content_raises(self):
        from xml.parsers.expat import ExpatError
        with pytest.raises(ExpatError):
            parse("<a>\x00</a>")

    def test_null_byte_in_bytes_input_raises(self):
        from xml.parsers.expat import ExpatError
        with pytest.raises(ExpatError):
            parse(b"<a>\x00</a>")

    def test_item_callback_exception_propagates(self):
        # An exception raised inside the callback should propagate to the caller
        def bad_cb(path, item):
            raise RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            parse("<a><b>1</b></a>", item_depth=2, item_callback=bad_cb)

    def test_postprocessor_returning_three_tuple_raises(self):
        # postprocessor must return (key, value) or None — 3-tuple → ValueError
        def pp(path, key, value):
            return key, value, "extra"
        with pytest.raises(ValueError):
            parse("<a>1</a>", postprocessor=pp)

    def test_postprocessor_returning_string_raises(self):
        # returning a bare string causes "too many values to unpack"
        def pp(path, key, value):
            return "bad"
        with pytest.raises(ValueError):
            parse("<a>1</a>", postprocessor=pp)

    def test_force_cdata_callable_exception_propagates(self):
        def bad(path, key, value):
            raise RuntimeError("force_cdata crash")
        with pytest.raises(RuntimeError, match="force_cdata crash"):
            parse("<a>text</a>", force_cdata=bad)

    def test_force_list_callable_exception_propagates(self):
        def bad(path, key, value):
            raise RuntimeError("force_list crash")
        with pytest.raises(RuntimeError, match="force_list crash"):
            parse("<a><b>1</b></a>", force_list=bad)


class TestUnparseCrashVectors:
    def test_none_input_raises_attributeerror(self):
        # None has no .items() — documents current failure mode
        with pytest.raises(AttributeError):
            unparse(None)

    def test_list_input_raises_attributeerror(self):
        with pytest.raises(AttributeError):
            unparse(["a", "b"])

    def test_string_input_raises(self):
        # str.items() doesn't exist
        with pytest.raises(AttributeError):
            unparse("not a dict")

    def test_empty_element_name_produces_invalid_xml(self):
        # _validate_name does not reject ""; documents the gap so it can be
        # caught if/when the validator is tightened
        xml = unparse({"": "x"}, full_document=False)
        # The output is currently `<>x</>` — note: not valid XML
        assert "<>" in xml or xml == ""  # either fix is acceptable

    def test_empty_attr_name_after_prefix_strip(self):
        # {"@": "x"} strips the prefix leaving attr name "" — produces `<a ="x">`
        xml = unparse({"a": {"@": "x"}}, full_document=False)
        # Documents current behaviour; a future fix should raise ValueError
        assert 'a' in xml  # element is still emitted

    def test_circular_reference_raises_recursion_error(self):
        import sys
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(200)
        try:
            d: dict = {}
            d["a"] = d
            with pytest.raises(RecursionError):
                unparse(d, full_document=False)
        finally:
            sys.setrecursionlimit(old_limit)

    def test_preprocessor_returning_three_tuple_raises(self):
        def pp(key, value):
            return key, value, "extra"
        with pytest.raises(ValueError):
            unparse({"a": "1"}, preprocessor=pp, full_document=False)

    def test_preprocessor_returning_string_raises(self):
        def pp(key, value):
            return "bad"
        with pytest.raises(ValueError):
            unparse({"a": "1"}, preprocessor=pp, full_document=False)

    def test_invalid_bytes_errors_handler_raises(self):
        with pytest.raises(ValueError, match="Invalid bytes_errors handler"):
            unparse({"a": "x"}, bytes_errors="nonexistent")

    def test_comment_with_integer_value_is_skipped_or_emitted(self):
        # Integer comment values go through _convert_value_to_string → safe
        xml = unparse({"a": {"#comment": 42, "b": "1"}}, full_document=False)
        assert "<b>1</b>" in xml  # element content is preserved

    def test_comment_none_value_is_silently_skipped(self):
        xml = unparse({"a": {"#comment": None, "b": "1"}}, full_document=False)
        assert "<!--" not in xml
        assert "<b>1</b>" in xml
