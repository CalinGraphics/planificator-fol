"""Command-line interface.

    python -m planner solve    DOMAIN PROBLEM [-a astar] [-H hff] [--trace]
    python -m planner validate DOMAIN PROBLEM PLAN [--trace]
    python -m planner compare  DOMAIN PROBLEM [--configs bfs ucs astar:hmax ...]
    python -m planner query    DOMAIN PROBLEM "Above(x, D)" [--after PLAN]
    python -m planner inspect  DOMAIN PROBLEM
    python -m planner unify    "P(x, f(y))" "P(A, f(B))"
    python -m planner mcp      (Model Context Protocol server on stdio)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .logic.parser import ParseError, parse_formula, parse_term
from .logic.reasoning import LogicError, answer_query
from .logic.syntax import free_vars, to_text
from .logic.unify import resolve, unify
from .planning.domain import PlanningError, load_domain, load_problem
from .planning.heuristics import HEURISTICS
from .planning.report import (
    comparison_table, describe_algorithm, format_result, format_task, format_trace,
    plan_file_text, result_to_dict,
)
from .planning.search import ALGORITHMS, INFORMED, Searcher
from .planning.task import PlanningTask
from .planning.validate import parse_plan, validate_plan

DEFAULT_CONFIGS = ["bfs", "iddfs", "ucs", "greedy:goal_count", "greedy:hff",
                   "astar:hmax", "astar:hadd", "astar:hff", "wastar:hff"]


def _load(args, check_axioms=False) -> PlanningTask:
    domain = load_domain(args.domain)
    problem = load_problem(args.problem)
    return PlanningTask(domain, problem, check_axioms=check_axioms)


def _steps(plan):
    return [(a.schema.name, tuple(str(x) for x in a.args)) for a in plan]


def cmd_solve(args) -> int:
    task = _load(args, args.check_axioms)
    searcher = Searcher(task, args.mode, args.max_expansions, args.timeout)
    result = searcher.run(args.algorithm, args.heuristic, args.weight, args.depth_limit)
    report = validate_plan(task, _steps(result.plan)) if result.solved else None
    if args.json:
        print(json.dumps(result_to_dict(task, result, args.mode, report), indent=2))
    else:
        print(format_result(task, result, args.mode))
        if report is not None:
            print()
            print(("Validation OK: " if report.valid else "VALIDATION FAILED: ") + report.message)
            if args.trace:
                print()
                print("Trace (+ added, - deleted facts)")
                print(format_trace(report))
    if args.output and result.solved:
        Path(args.output).write_text(plan_file_text(task, result), encoding="utf-8")
        if not args.json:
            print(f"\nPlan written to {args.output}")
    return 0 if result.solved and report.valid else 1


def cmd_validate(args) -> int:
    task = _load(args)
    steps = parse_plan(Path(args.plan).read_text(encoding="utf-8"))
    report = validate_plan(task, steps)
    if args.json:
        print(json.dumps({"valid": report.valid, "message": report.message,
                          "cost": report.cost, "steps": len(report.steps)}, indent=2))
    else:
        if args.trace or not report.valid:
            print(format_trace(report))
            print()
        print(("VALID: " if report.valid else "INVALID: ") + report.message)
    return 0 if report.valid else 1


def cmd_compare(args) -> int:
    task = _load(args, args.check_axioms)
    rows = []
    results = []
    for cfg in args.configs:
        alg, _, h = cfg.partition(":")
        if alg not in ALGORITHMS:
            raise SystemExit(f"unknown algorithm in {cfg!r}")
        if alg in INFORMED and not h:
            h = "hff"
        searcher = Searcher(task, args.mode, args.max_expansions, args.timeout)
        r = searcher.run(alg, h or None, args.weight)
        rows.append((describe_algorithm(r), r))
        results.append(result_to_dict(task, r, args.mode))
        if not args.json:
            print(f"  {describe_algorithm(r):<40} {r.status:<12} "
                  f"{(len(r.plan) if r.solved else '-')!s:>5} steps   {r.stats.elapsed:7.3f} s",
                  file=sys.stderr)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"\n{task.domain.name} / {task.problem.name}\n")
        print(comparison_table(rows))
    return 0


def cmd_query(args) -> int:
    task = _load(args)
    state = task.init
    if args.after:
        steps = parse_plan(Path(args.after).read_text(encoding="utf-8"))
        report = validate_plan(task, steps)
        if not all(s.ok for s in report.steps) or len(report.steps) != len(steps):
            print("INVALID plan: " + report.message)
            return 1
        state = report.final_state
    query = parse_formula(args.query)
    model = task.model(state)
    fv = sorted(free_vars(query), key=lambda v: v.name)
    answers = answer_query(query, model)
    where = "after the plan" if args.after else "in the initial state"
    if not fv:
        print(f"{to_text(query)}  is  {'TRUE' if answers else 'FALSE'}  {where}")
        return 0
    print(f"{to_text(query)}   ({len(answers)} answer(s) {where})")
    for theta in answers:
        print("  " + ", ".join(f"{v} = {theta[v]}" for v in fv))
    return 0


def cmd_inspect(args) -> int:
    task = _load(args)
    print(format_task(task, unicode=not args.ascii))
    return 0


def cmd_unify(args) -> int:
    # Atoms and terms share the same syntax P(t1, ..., tn), so both sides are
    # read as terms; unifying P(...) with P(...) is the same either way.
    x, y = parse_term(args.x), parse_term(args.y)
    theta = unify(x, y)
    if theta is None:
        print(f"UNIFY({to_text(x)}, {to_text(y)}) = failure")
        return 1
    theta = resolve(theta)
    if not theta:
        print(f"UNIFY({to_text(x)}, {to_text(y)}) = {{}}  (already identical)")
        return 0
    pairs = ", ".join(f"{v}/{to_text(t)}" for v, t in sorted(theta.items(), key=lambda kv: kv[0].name))
    print(f"UNIFY({to_text(x)}, {to_text(y)}) = {{{pairs}}}")
    return 0


def cmd_mcp(args) -> int:
    from .mcp_server import serve
    serve()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="planner",
        description="A planner that combines first-order logic knowledge representation "
                    "with state-space search.")
    sub = p.add_subparsers(dest="command", required=True)

    def task_args(sp):
        sp.add_argument("domain", help="domain file (.fol)")
        sp.add_argument("problem", help="problem file (.fol)")

    def search_args(sp):
        sp.add_argument("--mode", choices=("grounded", "lifted"), default="grounded",
                        help="how successors are generated (default: grounded)")
        sp.add_argument("--max-expansions", type=int, default=500_000)
        sp.add_argument("--timeout", type=float, default=120.0, help="seconds")
        sp.add_argument("-w", "--weight", type=float, default=2.0, help="weight for wastar")
        sp.add_argument("--check-axioms", action="store_true",
                        help="also check domain axioms on every generated state")
        sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("solve", help="find a plan")
    task_args(sp)
    sp.add_argument("-a", "--algorithm", choices=ALGORITHMS, default="astar")
    sp.add_argument("-H", "--heuristic", choices=HEURISTICS, default="hff")
    sp.add_argument("--depth-limit", type=int, default=None, help="for dfs / iddfs")
    sp.add_argument("--trace", action="store_true", help="show the facts each step changes")
    sp.add_argument("-o", "--output", help="write the plan to this file")
    search_args(sp)
    sp.set_defaults(func=cmd_solve)

    sp = sub.add_parser("validate", help="check a plan against the domain")
    task_args(sp)
    sp.add_argument("plan", help="plan file, one action per line")
    sp.add_argument("--trace", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("compare", help="run several algorithms on one problem")
    task_args(sp)
    sp.add_argument("--configs", nargs="+", default=DEFAULT_CONFIGS,
                    help="algorithm[:heuristic] items, e.g. bfs astar:hmax greedy:hff")
    search_args(sp)
    sp.set_defaults(func=cmd_compare, timeout=30.0, max_expansions=100_000)

    sp = sub.add_parser("query", help="ask a FOL query about a state")
    task_args(sp)
    sp.add_argument("query", help='e.g. "Above(x, D)" or "exists x: On(x, A)"')
    sp.add_argument("--after", metavar="PLAN", help="evaluate in the state reached by this plan")
    sp.set_defaults(func=cmd_query)

    sp = sub.add_parser("inspect", help="show the parsed domain and problem")
    task_args(sp)
    sp.add_argument("--ascii", action="store_true", help="ASCII instead of logic symbols")
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("unify", help="compute the most general unifier of two terms or atoms")
    sp.add_argument("x")
    sp.add_argument("y")
    sp.set_defaults(func=cmd_unify)

    sp = sub.add_parser("mcp", help="run as a Model Context Protocol server (stdio)")
    sp.set_defaults(func=cmd_mcp)
    return p


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ParseError, PlanningError, LogicError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"error: {e.strerror}: {e.filename}", file=sys.stderr)
        return 2
