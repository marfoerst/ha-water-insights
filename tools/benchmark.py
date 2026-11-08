"""Offline benchmark: how well would the model do on your data?

Usage:
    python -m tools.benchmark path/to/export.json|events.jsonl [--tz Europe/Berlin]
    python -m tools.benchmark --synthetic

Prints per-category metrics measured chronologically (newest draws held out)
and the v1 release verdict (>= 90 % precision for Dusche, Toilette,
Waschmaschine when automatically classified).
"""
from __future__ import annotations

import argparse
from collections import Counter
import sys
from zoneinfo import ZoneInfo

from custom_components.water_insights.evaluation import train_and_evaluate
from custom_components.water_insights.storage import State, import_events

MAIN = ("Dusche", "Toilette", "Waschmaschine")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path", nargs="?")
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--tz", default="Europe/Berlin")
    p.add_argument("--threshold", type=float, default=0.8)
    a = p.parse_args(argv)
    if a.synthetic:
        from tools.synth import HELPER, generate  # noqa: PLC0415

        events = generate(60, seed=1)
        helpers = [HELPER]
    elif a.path:
        state = State()
        with open(a.path, encoding="utf-8") as fh:
            import_events(state, fh.read())
        events = list(state.events.values())
        helpers = sorted({h for e in events for h in e.helpers})
    else:
        p.print_help()
        return 2
    labels = Counter(e.label for e in events if e.label)
    cats = [c for c in labels if c != "mixed"]
    res = train_and_evaluate(events, cats, helpers, ZoneInfo(a.tz), conf_threshold=a.threshold)
    print(f"{len(events)} draws, {sum(labels.values())} labeled, helpers: {helpers or '-'}")
    if not res.ok:
        print(f"Not trainable: {res.reason} (need >= 10 labeled draws in >= 2 categories)")
        for c, m in res.per_class.items():
            print(f"  {c:16} {m.n_total:5}")
        return 1
    print(f"model: {res.chosen}\n")
    print(f"  {'category':16} {'n':>5} {'precision':>10} {'auto prec.':>10} {'auto share':>10}  auto")
    fmt = lambda v: "   –" if v is None else f"{v * 100:7.0f} %"  # noqa: E731
    for c, m in sorted(res.per_class.items(), key=lambda kv: -kv[1].n_total):
        print(f"  {c:16} {m.n_total:5} {fmt(m.precision):>10} {fmt(m.auto_precision):>10} {fmt(m.auto_coverage):>10}  {'yes' if m.enabled else 'no'}")
    print()
    for c in MAIN:
        m = res.per_class.get(c)
        if m is None or m.auto_precision is None:
            print(f"  {c}: not measurable yet")
        else:
            ok = "meets" if m.auto_precision >= 0.9 else "misses (stays open)"
            print(f"  {c}: {m.auto_precision * 100:.0f} % -> {ok} the 90 % bar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
