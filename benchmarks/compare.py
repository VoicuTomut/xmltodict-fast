#!/usr/bin/env python3
"""
Compare xmltodict benchmark result files — any number of them.

Usage:
    # compare everything in a directory (alphabetical order)
    python benchmarks/compare.py benchmarks/results/

    # compare specific files in a custom order
    python benchmarks/compare.py results/baseline.json results/after_p2.json results/after_p3.json

    # show only one metric section
    python benchmarks/compare.py benchmarks/results/ --metric parse
    python benchmarks/compare.py benchmarks/results/ --metric element
    python benchmarks/compare.py benchmarks/results/ --metric memory
    python benchmarks/compare.py benchmarks/results/ --metric unparse
"""

import argparse
import json
import sys
from pathlib import Path

# ── colour helpers ────────────────────────────────────────────────────────────

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

MEDALS = ["🥇", "🥈", "🥉"]


def _green(s):  return f"{GREEN}{BOLD}{s}{RESET}"
def _red(s):    return f"{RED}{s}{RESET}"
def _dim(s):    return f"{DIM}{s}{RESET}"
def _bold(s):   return f"{BOLD}{s}{RESET}"


# ── loading ───────────────────────────────────────────────────────────────────

def load_all(paths: list[Path]) -> tuple[list[str], dict[str, dict]]:
    """Return (ordered names, name→data dict)."""
    names, data = [], {}
    for p in paths:
        try:
            payload = json.loads(p.read_text())
            name = p.stem
            names.append(name)
            data[name] = payload
        except Exception as exc:
            print(f"  skip {p.name}: {exc}", file=sys.stderr)
    return names, data


# ── ranking helpers ───────────────────────────────────────────────────────────

def _ranks(values: list[float | None], higher_is_better: bool) -> list[int | None]:
    """
    Return rank for each position (0 = best).
    None values get rank None.
    """
    indexed = [(i, v) for i, v in enumerate(values) if v is not None]
    indexed.sort(key=lambda x: x[1], reverse=higher_is_better)
    ranks: list[int | None] = [None] * len(values)
    for rank, (idx, _) in enumerate(indexed):
        ranks[idx] = rank
    return ranks


def _fmt(v: float | None, decimals: int) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}"


# ── table printing ────────────────────────────────────────────────────────────

COL_LABEL = 28
COL_VAL   = 12


def _bar(names: list[str]) -> str:
    return "  " + "-" * (COL_LABEL + len(names) * (COL_VAL + 2) + 10)


def _header_row(names: list[str]) -> str:
    h = f"  {'':>{COL_LABEL}}"
    for n in names:
        h += f"  {n:>{COL_VAL}}"
    h += f"  {'winner'}"
    return h


def _data_row(
    label: str,
    values: list[float | None],
    names: list[str],
    unit: str,
    decimals: int,
    higher_is_better: bool,
) -> str:
    ranks = _ranks(values, higher_is_better)

    valid = [v for v in values if v is not None]
    winner_idx = ranks.index(0) if 0 in ranks else None

    row = f"  {label:<{COL_LABEL}}"
    for v, r in zip(values, ranks):
        cell = f"{_fmt(v, decimals)} {unit}"
        if r == 0:
            cell = _green(cell)
        elif r is not None and r == len([x for x in ranks if x is not None]) - 1:
            cell = _red(cell)
        # right-pad to column width (ANSI codes don't count toward width)
        visible_len = len(_fmt(v, decimals)) + 1 + len(unit)
        pad = max(0, COL_VAL - visible_len)
        row += "  " + " " * pad + cell

    # winner annotation
    if winner_idx is not None:
        medal = MEDALS[0]
        row += f"  {medal} {_bold(names[winner_idx])}"
        if len(valid) > 1:
            # gap from winner to second place
            ranked_vals = sorted(
                [v for v in values if v is not None],
                reverse=higher_is_better,
            )
            if len(ranked_vals) > 1:
                second = ranked_vals[1]
                winner = ranked_vals[0]
                if second and second != 0:
                    gap = abs(winner - second) / second * 100
                    row += _dim(f"  ({gap:.0f}% ahead of 2nd)")

    return row


def section(title: str) -> None:
    print()
    print("═" * 72)
    print(f"  {_bold(title)}")
    print("═" * 72)


def print_metric_table(
    names: list[str],
    data: dict[str, dict],
    section_title: str,
    data_key: str,
    fixture_key: str,
    unit: str,
    decimals: int,
    higher_is_better: bool,
    fixtures: list[str] | None = None,
) -> list[tuple[str, int | None]]:
    """
    Print a table for one metric.
    Returns list of (fixture_label, winner_name_index) for the summary.
    """
    section(section_title)
    print(_header_row(names))
    print(_bar(names))

    if fixtures is None:
        all_fx: set[str] = set()
        for n in names:
            all_fx |= set(data[n].get(data_key, {}).keys())
        fixtures = sorted(all_fx)

    winners = []
    for fx in fixtures:
        values = []
        for n in names:
            entry = data[n].get(data_key, {}).get(fx)
            if isinstance(entry, dict):
                v = entry.get(fixture_key)
            else:
                v = entry  # per_element_us stores floats directly
            values.append(v)

        print(_data_row(fx, values, names, unit, decimals, higher_is_better))
        ranks = _ranks(values, higher_is_better)
        winner_idx = ranks.index(0) if 0 in ranks else None
        winners.append((fx, winner_idx))

    return winners


# ── section-specific helpers ──────────────────────────────────────────────────

def parse_section(names, data):
    FIXTURES = ["small.xml", "medium.xml", "large.xml", "wide.xml", "deep.xml", "namespaced.xml"]
    return print_metric_table(
        names, data,
        "PARSE throughput  (MB/s — higher is better)",
        "parse_throughput", "throughput_mbs",
        "MB/s", 1, True,
        fixtures=[f for f in FIXTURES if any(f in data[n].get("parse_throughput", {}) for n in names)],
    )


def element_section(names, data):
    section("PER-ELEMENT cost  (µs/element — lower is better)")
    print(_header_row(names))
    print(_bar(names))

    all_ns: set[str] = set()
    for n in names:
        all_ns |= set(data[n].get("per_element_us", {}).keys())
    ns = sorted(all_ns, key=lambda x: int(x))

    winners = []
    for size in ns:
        values = []
        for n in names:
            v = data[n].get("per_element_us", {}).get(size)
            values.append(v)
        label = f"N = {int(size):,}"
        print(_data_row(label, values, names, "µs", 2, False))
        ranks = _ranks(values, False)
        winners.append((label, ranks.index(0) if 0 in ranks else None))
    return winners


def memory_section(names, data):
    section("MEMORY ratio  (lower is better)")

    # non-streaming
    ns_fixtures = ["medium.xml_non_streaming", "large.xml_non_streaming", "wide.xml_non_streaming"]
    present = [f for f in ns_fixtures if any(f in data[n].get("memory_ratio", {}) for n in names)]

    winners = []
    if present:
        print(_dim("  Non-streaming  (× input size):"))
        print(_header_row(names))
        print(_bar(names))
        for fx in present:
            values = [data[n].get("memory_ratio", {}).get(fx, {}).get("ratio") for n in names]
            print(_data_row(fx.replace("_non_streaming", ""), values, names, "×", 1, False))
            ranks = _ranks(values, False)
            winners.append((fx, ranks.index(0) if 0 in ranks else None))

    # streaming overhead
    stream_fixtures = ["medium.xml_streaming", "large.xml_streaming", "wide.xml_streaming"]
    present_s = [f for f in stream_fixtures if any(f in data[n].get("memory_ratio", {}) for n in names)]
    if present_s:
        print()
        print(_dim("  Streaming overhead  (KB — constant = good):"))
        print(_header_row(names))
        print(_bar(names))
        for fx in present_s:
            values = [data[n].get("memory_ratio", {}).get(fx, {}).get("overhead_kb") for n in names]
            print(_data_row(fx.replace("_streaming", " (stream)"), values, names, "KB", 0, False))
            ranks = _ranks(values, False)
            winners.append((fx, ranks.index(0) if 0 in ranks else None))

    return winners


def unparse_section(names, data):
    FIXTURES = ["medium.xml", "large.xml", "wide.xml"]
    return print_metric_table(
        names, data,
        "UNPARSE throughput  (MB/s — higher is better)",
        "unparse_throughput", "throughput_mbs",
        "MB/s", 1, True,
        fixtures=[f for f in FIXTURES if any(f in data[n].get("unparse_throughput", {}) for n in names)],
    )


# ── winner summary ────────────────────────────────────────────────────────────

def winner_summary(names: list[str], all_winners: list[tuple[str, int | None]]) -> None:
    section("WINNER SUMMARY — wins across all fixtures and metrics")

    tally: dict[str, int] = {n: 0 for n in names}
    for _, winner_idx in all_winners:
        if winner_idx is not None:
            tally[names[winner_idx]] += 1

    total = len([w for _, w in all_winners if w is not None])
    sorted_names = sorted(tally, key=lambda n: tally[n], reverse=True)

    for i, n in enumerate(sorted_names):
        medal = MEDALS[i] if i < 3 else "   "
        wins = tally[n]
        bar_len = int(wins / max(total, 1) * 30) if wins else 0
        bar = "█" * bar_len
        pct = f"{wins / total * 100:.0f}%" if total else "0%"
        colour = GREEN if i == 0 else (DIM if wins == 0 else "")
        print(f"  {medal}  {colour}{_bold(n):<22}{RESET}  {wins:>3} / {total} wins  ({pct})  {_green(bar) if i == 0 else bar}")

    print()
    if sorted_names:
        overall = sorted_names[0]
        print(f"  {_green('Best overall: ' + overall)}")
    print()


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Compare xmltodict benchmark result files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "paths",
        nargs="+",
        help="Directory of .json files, or individual .json paths.",
    )
    ap.add_argument(
        "--metric",
        choices=["parse", "element", "memory", "unparse"],
        default=None,
        help="Show only this metric section (default: all).",
    )
    args = ap.parse_args()

    json_files: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():
            json_files.extend(sorted(p.glob("*.json")))
        elif p.suffix == ".json":
            json_files.append(p)
        else:
            ap.error(f"Not a .json file or directory: {raw}")

    if not json_files:
        ap.error("No .json result files found.")

    names, data = load_all(json_files)
    if not names:
        print("No valid result files loaded.", file=sys.stderr)
        sys.exit(1)

    print()
    print(f"  {_bold('xmltodict benchmark comparison')}")
    print(f"  Comparing: {', '.join(names)}")

    mf = args.metric
    all_winners: list[tuple[str, int | None]] = []

    if mf in (None, "parse"):
        all_winners.extend(parse_section(names, data))

    if mf in (None, "element"):
        all_winners.extend(element_section(names, data))

    if mf in (None, "memory"):
        all_winners.extend(memory_section(names, data))

    if mf in (None, "unparse"):
        all_winners.extend(unparse_section(names, data))

    if len(names) > 1:
        winner_summary(names, all_winners)


if __name__ == "__main__":
    main()
