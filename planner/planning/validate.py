"""Independent plan validator.

The validator does not reuse the grounded actions produced for search: it
re-instantiates every step from the action *schema*, checks the full FOL
precondition by model checking, applies the effects, checks every state
axiom in the new state, and finally checks the goal. A plan that passes is
valid with respect to the domain as written in the .fol file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..logic.parser import ParseError, parse_atom
from ..logic.reasoning import holds
from ..logic.syntax import Const, conjuncts, substitute, to_text
from .task import PlanningTask


@dataclass
class Step:
    index: int
    action: str
    ok: bool
    cost: float = 0.0
    added: list = field(default_factory=list)
    deleted: list = field(default_factory=list)
    failed_conditions: list = field(default_factory=list)
    violated_axioms: list = field(default_factory=list)
    message: str = ""


@dataclass
class ValidationReport:
    valid: bool
    steps: list
    goal_reached: bool
    cost: float
    final_state: frozenset
    message: str

    def summary(self) -> str:
        return self.message


def parse_plan(text: str) -> list:
    """Read a plan: one action per line, e.g. `Stack(A, B)` or `3. Stack(A, B)`.
    Blank lines and comments (`#` or `;`) are ignored."""
    steps = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].split(";", 1)[0].strip()
        if not line:
            continue
        head = line.split(maxsplit=1)
        if head and head[0].rstrip(".:").isdigit() and len(head) == 2:
            line = head[1]
        try:
            atom = parse_atom(line, lineno)
        except ParseError as e:
            raise ParseError(f"plan line {lineno}: {e.message}") from None
        if not atom.ground:
            raise ParseError(f"plan line {lineno}: step {line!r} contains variables")
        steps.append((atom.pred, tuple(str(a) for a in atom.args)))
    return steps


def validate_plan(task: PlanningTask, steps) -> ValidationReport:
    """Simulate `steps` (list of (action name, [argument names])) from the
    initial state and report the first problem, if any."""
    domain = task.domain
    state = task.init
    total = 0.0
    out = []

    init_violations = task.violated_axioms(state)
    if init_violations:
        msg = "the initial state violates: " + "; ".join(
            _describe_violation(a, c, k) for a, c, k in init_violations)
        return ValidationReport(False, out, False, total, state, msg)

    for i, (name, args) in enumerate(steps, start=1):
        label = f"{name}({', '.join(args)})" if args else name
        schema = domain.actions.get(name)
        if schema is None:
            out.append(Step(i, label, False, message=f"unknown action {name}"))
            return ValidationReport(False, out, False, total, state,
                                    f"step {i}: unknown action {name}")
        if len(args) != len(schema.params):
            msg = f"step {i}: {name} takes {len(schema.params)} argument(s), got {len(args)}"
            out.append(Step(i, label, False, message=msg))
            return ValidationReport(False, out, False, total, state, msg)
        unknown = [a for a in args if a not in task.object_types]
        if unknown:
            msg = f"step {i}: unknown object(s) {', '.join(unknown)}"
            out.append(Step(i, label, False, message=msg))
            return ValidationReport(False, out, False, total, state, msg)

        theta = {v: Const(a) for v, a in zip(schema.params, args)}
        model = task.model(state)
        failed = [to_text(substitute(c, theta)) for c in schema.full_precondition
                  if not holds(c, model, theta)]
        if failed:
            msg = f"step {i}: precondition of {label} is false: {', '.join(failed)}"
            out.append(Step(i, label, False, failed_conditions=failed, message=msg))
            return ValidationReport(False, out, False, total, state, msg)

        add = {substitute(a, theta) for a in schema.add}
        delete = {substitute(a, theta) for a in schema.delete}
        new_state = (state - delete) | add
        cost = schema.cost.evaluate(theta, task.values)
        total += cost
        step = Step(i, label, True, cost,
                    added=sorted(to_text(a) for a in new_state - state),
                    deleted=sorted(to_text(a) for a in state - new_state))
        violations = task.violated_axioms(new_state)
        if violations:
            step.ok = False
            step.violated_axioms = [_describe_violation(a, c, k) for a, c, k in violations]
            step.message = f"step {i}: {violations[0][2]} violated after {label}"
            out.append(step)
            return ValidationReport(False, out, False, total, new_state,
                                    f"{step.message}: {step.violated_axioms[0]}")
        out.append(step)
        state = new_state

    goal_ok = holds(task.goal, task.model(state))
    if not goal_ok:
        missing = [to_text(c) for c in conjuncts(task.problem.goal)
                   if not _closed_holds(task, c, state)]
        detail = f" (unsatisfied: {', '.join(missing)})" if missing else ""
        return ValidationReport(False, out, False, total, state,
                                f"all {len(out)} steps are executable, but the goal is not reached{detail}")
    return ValidationReport(True, out, True, total, state,
                            f"valid plan: {len(out)} steps, cost {_fmt(total)}; every precondition, "
                            f"axiom and constraint holds and the goal is reached")


def _closed_holds(task, f, state) -> bool:
    from ..logic.syntax import Exists, free_vars
    fv = free_vars(f)
    if fv:
        f = Exists(tuple(sorted(fv, key=lambda v: v.name)), f)
    return holds(f, task.model(state))


def _describe_violation(axiom, cex, kind="axiom") -> str:
    text = f"{kind} {to_text(axiom)}"
    if cex:
        text += "  [counterexample: " + ", ".join(f"{v} = {c}" for v, c in cex.items()) + "]"
    return text


def _fmt(x: float) -> str:
    return str(int(x)) if x == int(x) else f"{x:g}"
