"""
Generate XML fixture files used by the benchmark suite.

Run once before benchmarking:
    python benchmarks/generate_fixtures.py

Produces:
    benchmarks/fixtures/small.xml    ~  1 KB   (single API response)
    benchmarks/fixtures/medium.xml   ~500 KB   (RSS-style feed, many records)
    benchmarks/fixtures/large.xml    ~ 10 MB   (data export, many records)
    benchmarks/fixtures/wide.xml     ~  1 MB   (10 000 flat siblings)
    benchmarks/fixtures/deep.xml     ~  30 KB  (500 levels of nesting)
    benchmarks/fixtures/namespaced.xml ~ 500 KB (SOAP-style, namespaced)
"""

import os
import random
import string

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
os.makedirs(FIXTURES_DIR, exist_ok=True)


def _rand_text(length=20):
    return "".join(random.choices(string.ascii_lowercase + " ", k=length)).strip()


# ---------------------------------------------------------------------------
# small.xml  (~1 KB) — typical single AWS / REST XML API response
# ---------------------------------------------------------------------------
def make_small():
    return """\
<?xml version="1.0" encoding="utf-8"?>
<ListBucketResult>
  <Name>my-bucket</Name>
  <Prefix/>
  <MaxKeys>1000</MaxKeys>
  <IsTruncated>false</IsTruncated>
  <Contents>
    <Key>file1.txt</Key>
    <LastModified>2024-01-01T00:00:00.000Z</LastModified>
    <Size>1024</Size>
    <StorageClass>STANDARD</StorageClass>
  </Contents>
  <Contents>
    <Key>file2.txt</Key>
    <LastModified>2024-01-02T00:00:00.000Z</LastModified>
    <Size>2048</Size>
    <StorageClass>STANDARD</StorageClass>
  </Contents>
</ListBucketResult>
"""


# ---------------------------------------------------------------------------
# medium.xml (~500 KB) — RSS-style feed, 2 000 items
# ---------------------------------------------------------------------------
def make_medium(n_items=2000):
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<feed>",
             "  <title>Tech News</title>",
             "  <link>https://example.com</link>"]
    for i in range(n_items):
        lines += [
            "  <item>",
            f"    <id>{i}</id>",
            f"    <title>{_rand_text(30)}</title>",
            f"    <author>{_rand_text(15)}</author>",
            f"    <published>2024-{(i%12)+1:02d}-{(i%28)+1:02d}T00:00:00Z</published>",
            f"    <summary>{_rand_text(80)}</summary>",
            f"    <category>{_rand_text(10)}</category>",
            "  </item>",
        ]
    lines.append("</feed>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# large.xml (~10 MB) — data export, 40 000 records
# ---------------------------------------------------------------------------
def make_large(n_records=40000):
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<export>"]
    for i in range(n_records):
        lines += [
            "  <record>",
            f"    <id>{i}</id>",
            f"    <name>{_rand_text(20)}</name>",
            f"    <value>{random.randint(0, 100000)}</value>",
            f"    <status>{'active' if i % 2 == 0 else 'inactive'}</status>",
            f"    <timestamp>2024-01-01T{i%24:02d}:00:00Z</timestamp>",
            "  </record>",
        ]
    lines.append("</export>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# wide.xml (~1 MB) — 10 000 flat siblings under root
# ---------------------------------------------------------------------------
def make_wide(n=10000):
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<root>"]
    for i in range(n):
        lines.append(
            f'  <item id="{i}" type="node"><name>{_rand_text(15)}</name>'
            f'<value>{random.randint(0, 9999)}</value></item>'
        )
    lines.append("</root>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# deep.xml (~30 KB) — 500 levels of nesting
# ---------------------------------------------------------------------------
def make_deep(depth=500):
    open_tags = "\n".join(f"{'  ' * i}<level{i} depth=\"{i}\">" for i in range(depth))
    close_tags = "\n".join(
        f"{'  ' * i}</level{i}>" for i in range(depth - 1, -1, -1)
    )
    leaf = f"{'  ' * depth}<value>leaf</value>"
    return f'<?xml version="1.0" encoding="utf-8"?>\n{open_tags}\n{leaf}\n{close_tags}'


# ---------------------------------------------------------------------------
# namespaced.xml (~500 KB) — SOAP-style with namespace declarations
# ---------------------------------------------------------------------------
def make_namespaced(n_items=1500):
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"',
        '               xmlns:data="http://example.com/data"',
        '               xmlns:meta="http://example.com/meta">',
        "  <soap:Body>",
        "    <data:Response>",
    ]
    for i in range(n_items):
        lines += [
            "      <data:Item>",
            f'        <data:Id meta:type="integer">{i}</data:Id>',
            f"        <data:Name>{_rand_text(20)}</data:Name>",
            f'        <meta:Created meta:format="iso8601">2024-01-{(i%28)+1:02d}</meta:Created>',
            "      </data:Item>",
        ]
    lines += ["    </data:Response>", "  </soap:Body>", "</soap:Envelope>"]
    return "\n".join(lines)


if __name__ == "__main__":
    fixtures = {
        "small.xml": make_small,
        "medium.xml": make_medium,
        "large.xml": make_large,
        "wide.xml": make_wide,
        "deep.xml": make_deep,
        "namespaced.xml": make_namespaced,
    }
    for filename, fn in fixtures.items():
        path = os.path.join(FIXTURES_DIR, filename)
        print(f"Generating {filename} ...", end=" ", flush=True)
        content = fn()
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        size_kb = os.path.getsize(path) / 1024
        print(f"{size_kb:.1f} KB")
    print("Done. Fixtures written to benchmarks/fixtures/")
