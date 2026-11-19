"""Inference over finite Herbrand models under the closed-world assumption.

* `FactIndex` / `Model`  - a set of ground atoms plus the domain of objects.
* `solve`                - answers conjunctive queries by unification with
                           facts (backtracking, most-constrained atom first).
* `holds`                - model checking of an arbitrary FOL formula
                           (quantifiers range over the objects of the model).
* `Rule`, `forward_chain` - definite (Horn) clauses and their least fixpoint,
                           computed with semi-naive forward chaining.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

from .syntax import (
    And, Atom, Const, Equals, Exists, Forall, Formula, Iff, Implies, Not,
    Or, Var, conjuncts, free_vars, substitute, to_text,
)
from .unify import match


class LogicError(Exception):
    pass


class UnboundVariableError(LogicError):
    pass


# --------------------------------------------------------------------------
# Fact storage
# --------------------------------------------------------------------------


class FactIndex:
    """Ground atoms indexed by predicate and by (predicate, first argument)."""

    __slots__ = ("_by_pred", "_by_first", "_size")

    def __init__(self, facts: Iterable[Atom] = ()):
        self._by_pred: dict[str, set] = {}
        self._by_first: dict[tuple, set] = {}
        self._size = 0
        for f in facts:
            self.add(f)

    def add(self, atom: Atom) -> bool:
        bucket = self._by_pred.get(atom.pred)
        if bucket is None:
            bucket = self._by_pred[atom.pred] = set()
        elif atom in bucket:
            return False
        bucket.add(atom)
        if atom.args:
            key = (atom.pred, atom.args[0])
            first = self._by_first.get(key)
            if first is None:
                self._by_first[key] = {atom}
            else:
                first.add(atom)
        self._size += 1
        return True

    def __contains__(self, atom) -> bool:
        bucket = self._by_pred.get(atom.pred)
        return bucket is not None and atom in bucket

    def candidates(self, pred: str, first=None):
        if first is None:
            return self._by_pred.get(pred, ())
        return self._by_first.get((pred, first), ())

    def predicates(self) -> set:
        return {p for p, s in self._by_pred.items() if s}

    def __iter__(self):
        for bucket in self._by_pred.values():
            yield from bucket

    def __len__(self):
        return self._size


class Model:
    """A finite Herbrand interpretation: the union of several fact layers and
    the domain of discourse (objects). Anything not listed is false (CWA)."""

    __slots__ = ("layers", "objects")

    def __init__(self, layers, objects):
        self.layers = tuple(layers)
        self.objects = tuple(objects)

    def __contains__(self, atom) -> bool:
        for layer in self.layers:
            if atom in layer:
                return True
        return False

    def candidates(self, pred: str, first=None):
        layers = self.layers
        if len(layers) == 1:
            return layers[0].candidates(pred, first)
        return _chain(layers, pred, first)

    def facts(self) -> Iterator[Atom]:
        for layer in self.layers:
            yield from layer

    def extended(self, layer) -> "Model":
        return Model(self.layers + (layer,), self.objects)


def _chain(layers, pred, first):
    for layer in layers:
        yield from layer.candidates(pred, first)


# --------------------------------------------------------------------------
# Conjunctive queries
# --------------------------------------------------------------------------


def solve(literals, model: Model, theta: dict | None = None,
          enumerate_vars=()) -> Iterator[dict]:
    """Yield every substitution, extending theta, under which all literals hold.

    Positive atoms are solved by unification against the facts of the model.
    Variables listed in `enumerate_vars` that no atom binds are enumerated over
    the objects of the model. All other literals (negations, (in)equalities,
    nested quantified formulas) are tested by model checking as soon as their
    variables are bound, which prunes the search early.
    """
    atoms = []
    tests = []
    for lit in literals:
        if lit.__class__ is Atom:
            atoms.append(lit)
        else:
            tests.append((lit, free_vars(lit)))
    yield from _solve(atoms, tests, model, dict(theta) if theta else {},
                      tuple(enumerate_vars))


def _solve(atoms, tests, model, theta, enum_vars):
    if tests:
        pending = []
        for test, fv in tests:
            if fv.issubset(theta.keys()):
                if not holds(test, model, theta):
                    return
            else:
                pending.append((test, fv))
        tests = pending

    if atoms:
        index = _most_constrained(atoms, theta)
        atom = atoms[index]
        rest = atoms[:index] + atoms[index + 1:]
        first = None
        if atom.args:
            a0 = atom.args[0]
            if a0.__class__ is Var:
                first = theta.get(a0)
            elif a0.__class__ is Const or a0.ground:
                first = a0
        for fact in model.candidates(atom.pred, first):
            extended = match(atom, fact, theta)
            if extended is not None:
                yield from _solve(rest, tests, model, extended, enum_vars)
        return

    for v in enum_vars:
        if v not in theta:
            for obj in model.objects:
                extended = dict(theta)
                extended[v] = obj
                yield from _solve((), tests, model, extended, enum_vars)
            return

    if tests:
        unbound = set().union(*(fv for _, fv in tests)) - theta.keys()
        names = ", ".join(sorted(v.name for v in unbound))
        raise UnboundVariableError(f"variable(s) {names} are not bound by any atom")
    yield theta


def _most_constrained(atoms, theta) -> int:
    best, best_score = 0, -1
    for i, atom in enumerate(atoms):
        score = 0
        for a in atom.args:
            if a.__class__ is not Var or a in theta:
                score += 1
        if score > best_score:
            best, best_score = i, score
            if score == len(atom.args):
                break
    return best


# --------------------------------------------------------------------------
# Model checking
# --------------------------------------------------------------------------


def holds(f: Formula, model: Model, theta: dict | None = None) -> bool:
    """Truth of a formula in the model, with free variables bound by theta."""
    theta = theta or {}
    cls = f.__class__
    if cls is Atom:
        a = f if f.ground else substitute(f, theta)
        if not a.ground:
            raise UnboundVariableError(f"free variable in {a}")
        return a in model
    if cls is Not:
        return not holds(f.body, model, theta)
    if cls is And:
        for p in f.parts:
            if not holds(p, model, theta):
                return False
        return True
    if cls is Or:
        for p in f.parts:
            if holds(p, model, theta):
                return True
        return False
    if cls is Implies:
        return (not holds(f.left, model, theta)) or holds(f.right, model, theta)
    if cls is Iff:
        return holds(f.left, model, theta) == holds(f.right, model, theta)
    if cls is Equals:
        left = substitute(f.left, theta)
        right = substitute(f.right, theta)
        if free_vars(left) or free_vars(right):
            raise UnboundVariableError(f"free variable in {to_text(f)}")
        return left == right
    if cls is Exists:
        inner = _without(theta, f.vars)
        for _ in solve(conjuncts(f.body), model, inner, f.vars):
            return True
        return False
    if cls is Forall:
        return counterexample(f, model, theta) is None
    raise TypeError(f"not a formula: {f!r}")


def counterexample(f: Forall, model: Model, theta: dict | None = None):
    """For a universal formula, return a binding of its variables that
    falsifies the body, or None if the formula holds.

    `forall x: G(x) -> C(x)` is evaluated by enumerating only the bindings that
    satisfy the guard G (found by unification), instead of every object tuple.
    """
    inner = _without(theta or {}, f.vars)
    body = f.body
    if body.__class__ is Implies:
        guard, conclusion = conjuncts(body.left), body.right
    else:
        guard, conclusion = [], body
    for binding in solve(guard, model, inner, f.vars):
        if not holds(conclusion, model, binding):
            return {v: binding[v] for v in f.vars if v in binding}
    return None


def _without(theta: dict, variables) -> dict:
    if not theta:
        return {}
    if any(v in theta for v in variables):
        return {k: v for k, v in theta.items() if k not in variables}
    return theta


def answer_query(query: Formula, model: Model) -> list[dict]:
    """All bindings of the free variables of `query` that make it true."""
    fv = sorted(free_vars(query), key=lambda v: v.name)
    results = []
    seen = set()
    for theta in solve(conjuncts(query), model, {}, fv):
        key = tuple(theta[v] for v in fv)
        if key not in seen:
            seen.add(key)
            results.append({v: theta[v] for v in fv})
    return results


# --------------------------------------------------------------------------
# Horn rules and forward chaining
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """A definite clause  L1 & ... & Ln -> H  (universally closed).

    Body literals may be positive atoms, (in)equalities, or negated atoms of
    predicates that are not themselves derived (stratified negation).
    """

    head: Atom
    body: tuple
    line: int | None = None

    @staticmethod
    def from_formula(f: Formula, line: int | None = None) -> "Rule":
        while f.__class__ is Forall:
            f = f.body
        if f.__class__ is not Implies or f.right.__class__ is not Atom:
            raise LogicError("a rule must have the form  L1 & ... & Ln -> Head(...)")
        body = tuple(conjuncts(f.left))
        positive_vars = set()
        for lit in body:
            if lit.__class__ is Atom:
                positive_vars |= free_vars(lit)
            elif lit.__class__ is Equals:
                pass
            elif lit.__class__ is Not and lit.body.__class__ in (Atom, Equals):
                pass
            else:
                raise LogicError(
                    f"rule body may only contain literals, found {to_text(lit)}")
        for lit in body:
            if lit.__class__ is not Atom and not free_vars(lit) <= positive_vars:
                raise LogicError(
                    f"unsafe rule: variables of {to_text(lit)} must occur in a positive atom")
        if not free_vars(f.right) <= positive_vars:
            raise LogicError("unsafe rule: every head variable must occur in a positive body atom")
        return Rule(f.right, body, line)

    @property
    def body_predicates(self) -> set:
        out = set()
        for lit in self.body:
            if lit.__class__ is Atom:
                out.add(lit.pred)
            elif lit.__class__ is Not and lit.body.__class__ is Atom:
                out.add(lit.body.pred)
        return out

    def relaxed(self) -> "Rule":
        """Drop negated atoms (used for over-approximating reachability)."""
        body = tuple(l for l in self.body
                     if not (l.__class__ is Not and l.body.__class__ is Atom))
        return Rule(self.head, body, self.line)

    def as_formula(self) -> Formula:
        body = self.body[0] if len(self.body) == 1 else And(self.body)
        return Implies(body, self.head)

    def __str__(self):
        return to_text(self.as_formula())

    def pretty(self) -> str:
        return self.as_formula().pretty()


def forward_chain(rules, base: Model) -> FactIndex:
    """Least fixpoint of the rules over the base model (semi-naive evaluation).

    Returns only the newly derived facts. In each round after the first, a rule
    is fired only with at least one body atom matched against the facts derived
    in the previous round, so no inference is repeated needlessly.
    """
    derived = FactIndex()
    if not rules:
        return derived
    model = base.extended(derived)

    delta = FactIndex()
    for rule in rules:
        for theta in solve(rule.body, model):
            head = substitute(rule.head, theta)
            if head not in model:
                delta.add(head)

    while len(delta):
        for fact in delta:
            derived.add(fact)
        delta_preds = delta.predicates()
        new = FactIndex()
        for rule in rules:
            for i, lit in enumerate(rule.body):
                if lit.__class__ is not Atom or lit.pred not in delta_preds:
                    continue
                rest = rule.body[:i] + rule.body[i + 1:]
                for fact in delta.candidates(lit.pred):
                    theta0 = match(lit, fact, {})
                    if theta0 is None:
                        continue
                    for theta in solve(rest, model, theta0):
                        head = substitute(rule.head, theta)
                        if head not in model:
                            new.add(head)
        delta = new
    return derived
