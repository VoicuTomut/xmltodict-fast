"""Entity handling across quick-xml's separate text/reference events."""

import pytest

rs = pytest.importorskip("xmltodict._xmltodict_rs")


@pytest.mark.parametrize("strip", [True, False])
@pytest.mark.parametrize("separator", ["", "|"])
def test_entity_text_run(strip, separator):
    text = "  fish &amp; chips &#65;&#x42; &lt;&gt;&quot;&apos;  "
    expected = "  fish & chips AB <>\"'  "
    if strip:
        expected = expected.strip()
    assert rs.parse(
        f"<root>{text}</root>", strip_whitespace=strip,
        cdata_separator=separator,
    ) == {"root": expected}


def test_entity_run_before_cdata_and_child():
    assert rs.parse(
        "<root>a&amp;b<![CDATA[c]]><child/>d&#33;</root>",
        cdata_separator="|",
    ) == {"root": {"child": None, "#text": "a&b|c|d!"}}


@pytest.mark.parametrize("entity", ["&unknown;", "&#xZZ;", "&#0;"])
def test_invalid_reference(entity):
    with pytest.raises(ValueError):
        rs.parse(f"<root>{entity}</root>")


def test_entity_streaming():
    items = []

    def callback(path, item):
        items.append(item)
        return True

    rs.parse("<root><item>a &amp; b</item><item>&#65;</item></root>",
             item_depth=2, item_callback=callback)
    assert items == ["a & b", "A"]
