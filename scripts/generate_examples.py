"""Regenerate examples/plans/*.txt and docs/EXEMPLE.md.

    python scripts/generate_examples.py            (about 3-5 minutes)
    python scripts/generate_examples.py --quick    (fewer configurations)
"""

from __future__ import annotations

import argparse
import platform
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from planner.planning.domain import load_domain, load_problem  # noqa: E402
from planner.planning.report import (  # noqa: E402
    comparison_table, describe_algorithm, fmt_num, format_trace,
)
from planner.planning.search import Searcher  # noqa: E402
from planner.planning.task import PlanningTask  # noqa: E402
from planner.planning.validate import validate_plan  # noqa: E402

# (domain, problem, configuration used for the reference plan)
PROBLEMS = [
    ("blocks", "sussman", ("astar", "hmax")),
    ("blocks", "reverse4", ("astar", "hmax")),
    ("blocks", "derived_goal", ("astar", "hmax")),
    ("blocks", "one_tower", ("astar", "hmax")),
    ("blocks", "large8", ("astar", "hff")),
    ("scheduling", "project", ("astar", "hmax")),
    ("scheduling", "cyclic", ("astar", "hmax")),
    ("scheduling", "missing_skill", ("astar", "hmax")),
    ("delivery", "office", ("astar", "hmax")),
]

FULL = [("bfs", None), ("iddfs", None), ("ucs", None), ("greedy", "goal_count"),
        ("greedy", "hff"), ("astar", "hmax"), ("astar", "hadd"), ("astar", "hff"),
        ("wastar", "hff")]
QUICK = [("bfs", None), ("ucs", None), ("greedy", "hff"), ("astar", "hmax"), ("astar", "hff")]

DOMAIN_TITLES = {"blocks": "Blocks World", "scheduling": "Planificarea sarcinilor",
                 "delivery": "Robot de livrare"}


def header_comment(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            lines.append(line[1:].rstrip()[1:] if line[1:2] == " " else line[1:].rstrip())
        elif line.strip():
            break
    return "\n".join(lines).strip("\n")


def run(task, alg, h, timeout, max_exp):
    return Searcher(task, "grounded", max_exp, timeout).run(alg, h)


def steps_of(plan):
    return [(a.schema.name, tuple(str(x) for x in a.args)) for a in plan]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--max-expansions", type=int, default=100_000)
    args = ap.parse_args()
    configs = QUICK if args.quick else FULL

    plans_dir = ROOT / "examples" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    md = [
        "# Exemple de planuri generate",
        "",
        "Fișier generat automat de `scripts/generate_examples.py` "
        f"({date.today().isoformat()}, Python {platform.python_version()}). "
        "Toate planurile de mai jos au fost verificate de validatorul independent "
        "(`planner validate`): fiecare precondiție, axiomă și constrângere este adevărată "
        "la fiecare pas, iar scopul este atins.",
        "",
        f"Limite pentru tabelele comparative: {args.timeout:g} s și "
        f"{args.max_expansions} noduri expandate per rulare. `limit` / `timeout` înseamnă "
        "că algoritmul nu a terminat în aceste limite.",
        "",
    ]
    current_domain = None
    for domain_name, problem_name, (ref_alg, ref_h) in PROBLEMS:
        domain_path = ROOT / "domains" / domain_name / "domain.fol"
        problem_path = ROOT / "domains" / domain_name / f"{problem_name}.fol"
        domain = load_domain(domain_path)
        problem = load_problem(problem_path)
        task = PlanningTask(domain, problem)
        print(f"{domain_name}/{problem_name}", file=sys.stderr)

        if domain_name != current_domain:
            current_domain = domain_name
            md += [f"## {DOMAIN_TITLES[domain_name]}", ""]
        md += [f"### `{domain_name}/{problem_name}.fol` ({problem.name})", "",
               "```text", header_comment(problem_path), "```", "",
               f"Scop: `{problem.goal.pretty()}`", ""]

        ref = run(task, ref_alg, ref_h, 120.0, 2_000_000)
        md.append(f"Plan de referință ({describe_algorithm(ref)}):")
        md.append("")
        if ref.solved:
            report = validate_plan(task, steps_of(ref.plan))
            assert report.valid, report.message
            md += ["```text"]
            md += [f"{i:>2}. {a.name}" + (f"   (cost {fmt_num(a.cost)})" if a.cost != 1 else "")
                   for i, a in enumerate(ref.plan, 1)]
            md += [f"-- {len(ref.plan)} pași, cost total {fmt_num(ref.cost)}; "
                   f"{ref.stats.expanded} noduri expandate", "```", ""]
            text = [f"# {domain.name} / {problem.name}",
                    f"# found by {describe_algorithm(ref)}",
                    f"# steps: {len(ref.plan)}, cost: {fmt_num(ref.cost)}",
                    f"# validation: {report.message}", ""]
            text += [a.name for a in ref.plan]
            text += ["", "# Trace (+ added, - deleted facts):"]
            text += ["# " + line for line in format_trace(report).splitlines()]
            (plans_dir / f"{domain_name}_{problem_name}.txt").write_text(
                "\n".join(text) + "\n", encoding="utf-8")
        elif ref.status == "inconsistent":
            md += [f"> Baza de cunoștințe este inconsistentă: {ref.note}", ""]
        elif ref.status == "unsolvable":
            md += ["> Nu există plan. Analiza de accesibilitate relaxată arată că scopul nu "
                   f"poate fi atins nici ignorând efectele negative (h(init) = "
                   f"{fmt_num(ref.initial_h)}), deci nu se expandează niciun nod.", ""]
        else:
            md += [f"> Căutarea s-a oprit: {ref.status}.", ""]

        if ref.status in ("inconsistent", "unsolvable"):
            continue
        rows = []
        for alg, h in configs:
            r = run(task, alg, h, args.timeout, args.max_expansions)
            if r.solved:
                assert validate_plan(task, steps_of(r.plan)).valid
            rows.append((describe_algorithm(r), r))
        md += ["Comparație:", "", comparison_table(rows), ""]

    out = ROOT / "docs" / "EXEMPLE.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
