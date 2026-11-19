"""Planning domains and problems, and the parser for the .fol file format.

A domain file declares types, predicates, Horn rules (`derive:`), state
axioms (`axiom:`, invariants that must hold in every reachable state),
state constraints (`constraint:`, enforced during search) and action schemas
written in AIMA notation:

    action Stack(x: Block, y: Block)
      pre:  Holding(x) & Clear(y) & x != y
      eff:  On(x, y) & Clear(x) & HandEmpty & ~Holding(x) & ~Clear(y)
      cost: 1

A problem file lists typed objects, the initial state (a set of ground atoms,
closed-world assumption) and a goal, which may be any FOL formula; its free
variables are read existentially.

See docs/LIMBAJ.md for the full reference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..logic.parser import ParseError, Parser, is_variable_name, parse_formula
from ..logic.reasoning import LogicError, Rule
from ..logic.syntax import (
    Atom, Const, Equals, Fn, Formula, Not, Var, conj, conjuncts, free_vars,
    substitute, to_text,
)


class PlanningError(Exception):
    pass


# --------------------------------------------------------------------------
# Cost expressions:  1 | Size(t) * Rate(w) + 2
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CostExpr:
    op: str                     # 'num' | 'fn' | '+' | '-' | '*' | '/'
    value: object = None        # float for 'num', Atom for 'fn'
    left: "CostExpr | None" = None
    right: "CostExpr | None" = None

    def evaluate(self, theta, values: dict) -> float:
        if self.op == "num":
            return self.value
        if self.op == "fn":
            key = substitute(self.value, theta)
            if key not in values:
                raise PlanningError(f"no numeric value given for {to_text(key)} (see 'values:')")
            return values[key]
        a = self.left.evaluate(theta, values)
        b = self.right.evaluate(theta, values)
        if self.op == "+":
            return a + b
        if self.op == "-":
            return a - b
        if self.op == "*":
            return a * b
        return a / b

    def variables(self) -> frozenset:
        if self.op == "num":
            return frozenset()
        if self.op == "fn":
            return free_vars(self.value)
        return self.left.variables() | self.right.variables()

    def functions(self) -> set:
        if self.op == "num":
            return set()
        if self.op == "fn":
            return {self.value.pred}
        return self.left.functions() | self.right.functions()

    def __str__(self):
        if self.op == "num":
            v = self.value
            return str(int(v)) if v == int(v) else str(v)
        if self.op == "fn":
            return to_text(self.value)
        return f"({self.left} {self.op} {self.right})"


UNIT_COST = CostExpr("num", 1.0)


def parse_cost(text: str, line=None, source=None) -> CostExpr:
    p = Parser(text, line, source)

    def expr():
        left = term()
        while p.peek.kind in ("+", "-"):
            op = p.peek.kind
            p.i += 1
            left = CostExpr(op, left=left, right=term())
        return left

    def term():
        left = factor()
        while p.peek.kind in ("*", "/"):
            op = p.peek.kind
            p.i += 1
            left = CostExpr(op, left=left, right=factor())
        return left

    def factor():
        if p.accept("("):
            e = expr()
            p.expect(")", "')'")
            return e
        num = p.accept("NUM")
        if num:
            return CostExpr("num", float(num.text))
        t = p.term()
        if t.__class__ is Fn:
            return CostExpr("fn", Atom(t.name, t.args))
        if t.__class__ is Const:
            return CostExpr("fn", Atom(t.name, ()))
        p.error(f"unexpected variable {t} in cost expression")

    e = expr()
    p.finish()
    return e


# --------------------------------------------------------------------------
# Domain and problem
# --------------------------------------------------------------------------


@dataclass
class ActionSchema:
    name: str
    params: tuple                   # tuple[Var]
    types: tuple                    # tuple[str | None]
    pre: Formula
    add: tuple                      # positive effect atoms
    delete: tuple                   # atoms removed by the action
    cost: CostExpr = UNIT_COST
    line: int | None = None

    @property
    def type_atoms(self) -> list:
        return [Atom(t, (v,)) for v, t in zip(self.params, self.types) if t]

    @property
    def full_precondition(self) -> list:
        """Type constraints followed by the conjuncts of the precondition."""
        return self.type_atoms + conjuncts(self.pre)

    def signature(self) -> str:
        parts = [f"{v}: {t}" if t else str(v) for v, t in zip(self.params, self.types)]
        return f"{self.name}({', '.join(parts)})"

    def effect_formula(self) -> Formula:
        return conj(list(self.add) + [Not(a) for a in self.delete])


@dataclass
class Axiom:
    """A closed FOL formula about states.

    kind == "axiom":      a domain invariant. It is checked on the initial
                          state (consistency of the knowledge base) and on
                          every state of a plan by the validator; searching
                          with --check-axioms also checks it on every state.
    kind == "constraint": a safety constraint. States that violate it are
                          pruned during search.
    """
    formula: Formula
    line: int | None = None
    kind: str = "axiom"

    def __str__(self):
        return to_text(self.formula)


@dataclass
class Domain:
    name: str
    types: list = field(default_factory=list)
    constants: dict = field(default_factory=dict)      # name -> type | None
    predicates: dict = field(default_factory=dict)     # name -> arity
    rules: list = field(default_factory=list)          # list[Rule]
    axioms: list = field(default_factory=list)         # list[Axiom]
    actions: dict = field(default_factory=dict)        # name -> ActionSchema
    source: str | None = None

    @property
    def derived_predicates(self) -> set:
        return {r.head.pred for r in self.rules}

    @property
    def fluent_predicates(self) -> set:
        """Predicates changed by some action effect."""
        out = set()
        for a in self.actions.values():
            out |= {x.pred for x in a.add}
            out |= {x.pred for x in a.delete}
        return out

    def dependent_derived(self, base: set) -> set:
        """Derived predicates whose rules depend (transitively) on `base`."""
        result = set()
        changed = True
        while changed:
            changed = False
            for r in self.rules:
                if r.head.pred not in result and r.body_predicates & (base | result):
                    result.add(r.head.pred)
                    changed = True
        return result

    @property
    def fluent_derived_predicates(self) -> set:
        return self.dependent_derived(self.fluent_predicates)

    @property
    def static_predicates(self) -> set:
        """Predicates whose extension never changes (includes static derived)."""
        return set(self.predicates) - self.fluent_predicates - self.fluent_derived_predicates

    def predicate_kind(self, pred: str) -> str:
        if pred in self.fluent_predicates:
            return "fluent"
        if pred in self.fluent_derived_predicates:
            return "derived (fluent)"
        if pred in self.derived_predicates:
            return "derived (static)"
        if pred in self.types:
            return "type"
        return "static"


@dataclass
class Problem:
    name: str
    domain_name: str | None
    objects: dict = field(default_factory=dict)         # name -> type | None
    init: list = field(default_factory=list)            # list[Atom]
    goal: Formula | None = None
    values: dict = field(default_factory=dict)          # ground Atom -> float
    source: str | None = None


# --------------------------------------------------------------------------
# File parser
# --------------------------------------------------------------------------

_FIELDS = {"types", "constants", "predicates", "derive", "axiom", "constraint", "pre", "eff",
           "cost", "objects", "init", "goal", "values", "domain"}
_HEADERS = {"domain", "problem", "action"}
_STMT = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)(\s*:)?\s*(.*)$")


@dataclass
class _Statement:
    keyword: str
    is_field: bool
    text: str
    line: int


def _statements(text: str, source: str | None):
    stmts: list[_Statement] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        m = _STMT.match(line)
        if m:
            kw, colon, rest = m.group(1), m.group(2), m.group(3)
            if colon and kw in _FIELDS:
                stmts.append(_Statement(kw, True, rest, lineno))
                continue
            if not colon and kw in _HEADERS and not line[:1].isspace():
                stmts.append(_Statement(kw, False, rest, lineno))
                continue
        if not stmts:
            raise ParseError(f"unexpected text {line.strip()!r}", lineno, source)
        stmts[-1].text += " " + line.strip()
    return stmts


def _split_top_level(text: str, seps=",;"):
    """Split on separators that are not inside parentheses."""
    parts, depth, current = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch in seps and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _parse_typed_names(text: str, line, source) -> list:
    """`A, B, C : Block; Ana, Dan : Worker; Hall`  ->  [(name, type|None)]"""
    result = []
    for group in _split_top_level(text, ";"):
        if ":" in group:
            names, typ = group.rsplit(":", 1)
            typ = typ.strip()
        else:
            names, typ = group, None
        for name in _split_top_level(names, ","):
            if not re.fullmatch(r"[A-Za-z0-9_]+", name):
                raise ParseError(f"invalid object name {name!r}", line, source)
            if is_variable_name(name):
                raise ParseError(
                    f"object {name!r} must start with an uppercase letter or digit "
                    "(lowercase names are variables)", line, source)
            result.append((name, typ))
    return result


def _parse_action_header(text: str, line, source):
    m = re.fullmatch(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\((.*)\))?\s*", text)
    if not m:
        raise ParseError(f"malformed action header {text!r}", line, source)
    name, params_text = m.group(1), m.group(2) or ""
    params, types = [], []
    for item in _split_top_level(params_text, ","):
        if ":" in item:
            v, t = (s.strip() for s in item.split(":", 1))
        else:
            v, t = item.strip(), None
        if not is_variable_name(v):
            raise ParseError(f"action parameter {v!r} must be a variable (lowercase)", line, source)
        if Var(v) in params:
            raise ParseError(f"duplicate parameter {v!r}", line, source)
        params.append(Var(v))
        types.append(t)
    return name, tuple(params), tuple(types)


def _parse_effects(text: str, line, source):
    f = parse_formula(text, line, source)
    add, delete = [], []
    for lit in conjuncts(f):
        if lit.__class__ is Atom:
            add.append(lit)
        elif lit.__class__ is Not and lit.body.__class__ is Atom:
            delete.append(lit.body)
        else:
            raise ParseError(
                f"effects must be a conjunction of literals; cannot use {to_text(lit)}",
                line, source)
    return tuple(add), tuple(delete)


def parse_domain(text: str, source: str | None = None) -> Domain:
    domain = None
    current_action = None
    for st in _statements(text, source):
        if not st.is_field and st.keyword == "domain":
            domain = Domain(name=st.text.strip(), source=source)
            continue
        if domain is None:
            raise ParseError("the file must start with 'domain <Name>'", st.line, source)
        if not st.is_field and st.keyword == "action":
            name, params, types = _parse_action_header(st.text, st.line, source)
            if name in domain.actions:
                raise ParseError(f"action {name} defined twice", st.line, source)
            current_action = ActionSchema(name, params, types, conj([]), (), (), UNIT_COST, st.line)
            domain.actions[name] = current_action
            continue
        kw = st.keyword
        if kw in ("pre", "eff", "cost"):
            if current_action is None:
                raise ParseError(f"'{kw}:' outside of an action", st.line, source)
            if kw == "pre":
                current_action.pre = parse_formula(st.text, st.line, source)
            elif kw == "eff":
                current_action.add, current_action.delete = _parse_effects(st.text, st.line, source)
            else:
                current_action.cost = parse_cost(st.text, st.line, source)
            continue
        current_action = None
        if kw == "types":
            for t in _split_top_level(st.text, ","):
                domain.types.append(t)
                domain.predicates[t] = 1
        elif kw == "constants":
            for name, typ in _parse_typed_names(st.text, st.line, source):
                domain.constants[name] = typ
        elif kw == "predicates":
            for item in _split_top_level(st.text, ","):
                p = Parser(item, st.line, source)
                t = p.term()
                p.finish()
                pname = t.name
                arity = len(t.args) if t.__class__ is Fn else 0
                if pname in domain.predicates and domain.predicates[pname] != arity:
                    raise ParseError(f"predicate {pname} declared with two arities", st.line, source)
                domain.predicates[pname] = arity
        elif kw == "derive":
            f = parse_formula(st.text, st.line, source)
            try:
                domain.rules.append(Rule.from_formula(f, st.line))
            except LogicError as e:
                raise ParseError(str(e), st.line, source) from None
        elif kw in ("axiom", "constraint"):
            domain.axioms.append(Axiom(parse_formula(st.text, st.line, source), st.line, kw))
        else:
            raise ParseError(f"'{kw}:' is not allowed in a domain file", st.line, source)
    if domain is None:
        raise ParseError("empty domain file", None, source)
    validate_domain(domain)
    return domain


def parse_problem(text: str, source: str | None = None) -> Problem:
    problem = None
    for st in _statements(text, source):
        if not st.is_field and st.keyword == "problem":
            problem = Problem(name=st.text.strip(), domain_name=None, source=source)
            continue
        if problem is None:
            raise ParseError("the file must start with 'problem <Name>'", st.line, source)
        kw = st.keyword
        if kw == "domain":
            problem.domain_name = st.text.strip()
        elif kw == "objects":
            for name, typ in _parse_typed_names(st.text, st.line, source):
                if name in problem.objects:
                    raise ParseError(f"object {name} declared twice", st.line, source)
                problem.objects[name] = typ
        elif kw == "init":
            f = parse_formula(st.text, st.line, source)
            for lit in conjuncts(f):
                if lit.__class__ is not Atom or not lit.ground:
                    raise ParseError(
                        f"the initial state is a list of ground atoms (closed world); "
                        f"found {to_text(lit)}", st.line, source)
                problem.init.append(lit)
        elif kw == "goal":
            if problem.goal is not None:
                raise ParseError("goal given twice", st.line, source)
            problem.goal = parse_formula(st.text, st.line, source)
        elif kw == "values":
            f = parse_formula(st.text, st.line, source)
            for item in conjuncts(f):
                if item.__class__ is not Equals:
                    raise ParseError("values are written as  F(A, B) = 3", st.line, source)
                lhs, rhs = item.left, item.right
                if rhs.__class__ is not Const or not _is_number(rhs.name):
                    raise ParseError(f"right-hand side of {to_text(item)} must be a number",
                                     st.line, source)
                key = Atom(lhs.name, lhs.args) if lhs.__class__ is Fn else Atom(lhs.name, ())
                problem.values[key] = float(rhs.name)
        else:
            raise ParseError(f"'{kw}:' is not allowed in a problem file", st.line, source)
    if problem is None:
        raise ParseError("empty problem file", None, source)
    if problem.goal is None:
        raise ParseError("the problem has no goal", None, source)
    return problem


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def load_domain(path) -> Domain:
    path = Path(path)
    return parse_domain(path.read_text(encoding="utf-8"), str(path))


def load_problem(path) -> Problem:
    path = Path(path)
    return parse_problem(path.read_text(encoding="utf-8"), str(path))


# --------------------------------------------------------------------------
# Static checks
# --------------------------------------------------------------------------


def _check_atoms(f: Formula, domain: Domain, where: str):
    from ..logic.syntax import atoms_of
    for a in atoms_of(f):
        if a.pred not in domain.predicates:
            raise PlanningError(f"{where}: unknown predicate {a.pred} (declare it under 'predicates:')")
        if domain.predicates[a.pred] != len(a.args):
            raise PlanningError(
                f"{where}: {a.pred} expects {domain.predicates[a.pred]} argument(s), "
                f"got {len(a.args)} in {to_text(a)}")


def validate_domain(d: Domain):
    for t in d.types:
        if is_variable_name(t):
            raise PlanningError(f"type {t} must be capitalised")
    for name, typ in d.constants.items():
        if typ and typ not in d.types:
            raise PlanningError(f"constant {name} has undeclared type {typ}")
    derived = d.derived_predicates
    for r in d.rules:
        _check_atoms(r.as_formula(), d, f"rule (line {r.line})")
        for lit in r.body:
            if lit.__class__ is Not and lit.body.__class__ is Atom and lit.body.pred in derived:
                raise PlanningError(
                    f"rule (line {r.line}): negation of the derived predicate "
                    f"{lit.body.pred} is not supported")
    for ax in d.axioms:
        _check_atoms(ax.formula, d, f"{ax.kind} (line {ax.line})")
    for a in d.actions.values():
        where = f"action {a.name} (line {a.line})"
        for t in a.types:
            # a parameter may be typed by a type or by any static unary predicate
            if t and (d.predicates.get(t) != 1 or t not in d.static_predicates):
                raise PlanningError(f"{where}: {t} is not a type or static unary predicate")
        _check_atoms(a.pre, d, where)
        _check_atoms(a.effect_formula(), d, where)
        params = set(a.params)
        extra = free_vars(a.pre) - params
        if extra:
            raise PlanningError(f"{where}: precondition uses variables that are not "
                                f"parameters: {', '.join(sorted(v.name for v in extra))}")
        extra = free_vars(a.effect_formula()) - params
        if extra:
            raise PlanningError(f"{where}: effect uses variables that are not parameters: "
                                f"{', '.join(sorted(v.name for v in extra))}")
        extra = a.cost.variables() - params
        if extra:
            raise PlanningError(f"{where}: cost uses unknown variables")
        bad = {x.pred for x in a.add + a.delete} & derived
        if bad:
            raise PlanningError(f"{where}: derived predicate(s) {', '.join(sorted(bad))} "
                                f"cannot appear in effects; they follow from the rules")
        bad = {x.pred for x in a.add + a.delete} & set(d.types)
        if bad:
            raise PlanningError(f"{where}: types cannot be changed by actions")


def validate_problem(problem: Problem, domain: Domain):
    if problem.domain_name and problem.domain_name != domain.name:
        raise PlanningError(f"problem {problem.name} is for domain {problem.domain_name}, "
                            f"not {domain.name}")
    for name, typ in problem.objects.items():
        if typ and typ not in domain.types:
            raise PlanningError(f"object {name} has undeclared type {typ}")
        if name in domain.constants:
            raise PlanningError(f"object {name} is already a domain constant")
    known = set(problem.objects) | set(domain.constants)
    derived = domain.derived_predicates
    for a in problem.init:
        _check_atoms(a, domain, "init")
        if a.pred in derived:
            raise PlanningError(f"init: {to_text(a)} is a derived predicate; it is computed "
                                f"from the rules and must not be listed")
        for arg in a.args:
            if str(arg) not in known:
                raise PlanningError(f"init: unknown object {arg} in {to_text(a)}")
    _check_atoms(problem.goal, domain, "goal")
    for key in problem.values:
        for arg in key.args:
            if str(arg) not in known:
                raise PlanningError(f"values: unknown object {arg} in {to_text(key)}")
    for a in domain.actions.values():
        for fn in a.cost.functions():
            if not any(k.pred == fn for k in problem.values):
                raise PlanningError(f"action {a.name} uses cost function {fn}, "
                                    f"but the problem gives no values for it")
    # constants mentioned in formulas must exist
    from ..logic.syntax import atoms_of
    formulas = [problem.goal] + [ax.formula for ax in domain.axioms]
    for f in formulas:
        for atom in atoms_of(f):
            for arg in atom.args:
                if arg.__class__ is Const and arg.name not in known and not _is_number(arg.name):
                    raise PlanningError(f"unknown object {arg} in {to_text(f)}")
