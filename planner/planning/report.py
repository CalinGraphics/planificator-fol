"""Text and JSON rendering of tasks, plans and search results."""

from __future__ import annotations

from math import inf

from ..logic.syntax import to_text
from .search import ALGORITHM_NAMES, SearchResult
from .task import PlanningTask
from .validate import ValidationReport


def fmt_num(x) -> str:
    if x is None:
        return "-"
    if x == inf:
        return "inf"
    return str(int(x)) if float(x).is_integer() else f"{x:.2f}"


def describe_algorithm(result: SearchResult) -> str:
    name = ALGORITHM_NAMES.get(result.algorithm, result.algorithm)
    if result.heuristic:
        name += f", h = {result.heuristic}"
    if result.weight is not None:
        name += f", w = {fmt_num(result.weight)}"
    return name


def format_plan(plan, numbered: bool = True, with_cost: bool = True) -> str:
    lines = []
    width = len(str(len(plan)))
    for i, a in enumerate(plan, start=1):
        prefix = f"{i:>{width}}. " if numbered else ""
        cost = f"   [cost {fmt_num(a.cost)}]" if with_cost and a.cost != 1 else ""
        lines.append(f"{prefix}{a.name}{cost}")
    return "\n".join(lines)


def plan_file_text(task: PlanningTask, result: SearchResult) -> str:
    """Plan in the format read back by `validate`."""
    header = [
        f"# domain:  {task.domain.name}",
        f"# problem: {task.problem.name}",
        f"# found by {describe_algorithm(result)}",
        f"# steps: {len(result.plan)}, cost: {fmt_num(result.cost)}",
    ]
    return "\n".join(header + [a.name for a in result.plan]) + "\n"


def format_result(task: PlanningTask, result: SearchResult, mode: str) -> str:
    out = [
        f"Domain    {task.domain.name}",
        f"Problem   {task.problem.name}",
        f"Search    {describe_algorithm(result)}   (successors: {mode})",
    ]
    if result.initial_h is not None:
        out.append(f"h(init)   {fmt_num(result.initial_h)}")
    out.append("")
    s = result.stats
    if result.solved:
        out.append(f"Plan: {len(result.plan)} steps, total cost {fmt_num(result.cost)}")
        out.append(_indent(format_plan(result.plan)) if result.plan
                   else "  (the goal already holds in the initial state)")
    elif result.status == "inconsistent":
        out.append("Inconsistent knowledge base: " + result.note)
    elif result.status == "unsolvable":
        out.append("No plan exists: the reachable state space was exhausted"
                   + (f" ({result.note})" if result.note else "")
                   + (" (the goal is unreachable even in the delete relaxation)"
                      if result.initial_h == inf else ""))
    elif result.status == "limit":
        out.append("Stopped: expansion limit reached before a plan was found.")
    elif result.status == "timeout":
        out.append("Stopped: time limit reached before a plan was found.")
    out.append("")
    out.append(f"Expanded {s.expanded}   generated {s.generated}   "
               f"max frontier {s.max_frontier}   time {s.elapsed:.3f} s"
               + (f"   pruned by constraints {s.pruned_by_constraints}"
                  if s.pruned_by_constraints else ""))
    return "\n".join(out)


def format_trace(report: ValidationReport) -> str:
    lines = []
    for step in report.steps:
        status = "" if step.ok else "   <-- " + step.message
        lines.append(f"{step.index:>3}. {step.action}{status}")
        if step.added:
            lines.append("       + " + ", ".join(step.added))
        if step.deleted:
            lines.append("       - " + ", ".join(step.deleted))
        for f in step.failed_conditions:
            lines.append(f"       false: {f}")
        for v in step.violated_axioms:
            lines.append(f"       violated: {v}")
    return "\n".join(lines)


def format_task(task: PlanningTask, unicode: bool = True) -> str:
    d = task.domain
    show = (lambda f: f.pretty()) if unicode else (lambda f: to_text(f))
    lines = [f"Domain {d.name}", ""]
    lines.append("Predicates")
    for name, arity in d.predicates.items():
        lines.append(f"  {name + '/' + str(arity):<18} {d.predicate_kind(name)}")
    if d.rules:
        lines += ["", "Horn rules (universally quantified)"]
        lines += [f"  {show(r.as_formula())}" for r in d.rules]
    if d.axioms:
        lines += ["", "Axioms and constraints"]
        lines += [f"  [{ax.kind}] {show(ax.formula)}" for ax in d.axioms]
    lines += ["", "Actions"]
    for a in d.actions.values():
        lines.append(f"  {a.signature()}")
        lines.append(f"    pre:  {show(a.pre)}")
        lines.append(f"    eff:  {show(a.effect_formula())}")
        if str(a.cost) != "1":
            lines.append(f"    cost: {a.cost}")
    p = task.problem
    lines += ["", f"Problem {p.name}",
              f"  objects:       {len(task.objects)}",
              f"  initial state: {len(task.init)} fluent facts, "
              f"{len(task.static_facts)} static facts (incl. types and derived)",
              f"  goal:          {show(p.goal)}"]
    ga = task.ground_actions
    lines += [f"  ground actions reachable in the relaxation: {len(ga)}",
              f"  ground rule instances (fluent):             {len(task.ground_rules)}"]
    violations = task.violated_axioms(task.init)
    if violations:
        lines.append("  initial state violates:")
        for ax, cex, kind in violations:
            w = ", ".join(f"{v} = {c}" for v, c in cex.items())
            lines.append(f"    [{kind}] {show(ax)}" + (f"   counterexample: {w}" if w else ""))
    else:
        lines.append("  initial state satisfies every axiom and constraint")
    return "\n".join(lines)


def result_to_dict(task: PlanningTask, result: SearchResult, mode: str,
                   report: ValidationReport | None = None) -> dict:
    s = result.stats
    data = {
        "domain": task.domain.name,
        "problem": task.problem.name,
        "algorithm": result.algorithm,
        "heuristic": result.heuristic,
        "weight": result.weight,
        "mode": mode,
        "status": result.status,
        "plan": [a.name for a in result.plan],
        "steps": len(result.plan) if result.solved else None,
        "cost": result.cost if result.solved else None,
        "initial_h": None if result.initial_h in (None, inf) else result.initial_h,
        "note": result.note or None,
        "stats": {
            "expanded": s.expanded,
            "generated": s.generated,
            "max_frontier": s.max_frontier,
            "pruned_by_constraints": s.pruned_by_constraints,
            "seconds": round(s.elapsed, 4),
        },
    }
    if report is not None:
        data["validation"] = {"valid": report.valid, "message": report.message}
    return data


def comparison_table(rows) -> str:
    """Markdown table from a list of (label, SearchResult)."""
    header = ("| Algorithm | Status | Steps | Cost | Expanded | Generated | Time (s) |\n"
              "|---|---|---:|---:|---:|---:|---:|")
    lines = [header]
    for label, r in rows:
        steps = str(len(r.plan)) if r.solved else "-"
        cost = fmt_num(r.cost) if r.solved else "-"
        lines.append(f"| {label} | {r.status} | {steps} | {cost} | {r.stats.expanded} | "
                     f"{r.stats.generated} | {r.stats.elapsed:.3f} |")
    return "\n".join(lines)


def _indent(text: str, n: int = 2) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in text.splitlines())
