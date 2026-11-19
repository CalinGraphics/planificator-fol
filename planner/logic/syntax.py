"""Abstract syntax of first-order logic: terms, atoms and formulas.

Naming convention (as in Russell & Norvig, AIMA): an identifier that starts
with a lowercase letter is a variable; everything else (capitalised names and
numbers) is a constant, predicate or function symbol.

Terms and atoms are hashed eagerly because they are used as members of
frozensets (planning states) millions of times during search.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Mapping

# --------------------------------------------------------------------------
# Terms
# --------------------------------------------------------------------------


class Term:
    __slots__ = ()


class Var(Term):
    __slots__ = ("name", "_hash")

    def __init__(self, name: str):
        self.name = name
        self._hash = hash(("var", name))

    def __eq__(self, other):
        return other.__class__ is Var and other.name == self.name

    def __hash__(self):
        return self._hash

    def __lt__(self, other):
        return str(self) < str(other)

    def __repr__(self):
        return f"Var({self.name!r})"

    def __str__(self):
        return self.name


class Const(Term):
    __slots__ = ("name", "_hash")

    def __init__(self, name: str):
        self.name = name
        self._hash = hash(("const", name))

    def __eq__(self, other):
        return other.__class__ is Const and other.name == self.name

    def __hash__(self):
        return self._hash

    def __lt__(self, other):
        return str(self) < str(other)

    def __repr__(self):
        return f"Const({self.name!r})"

    def __str__(self):
        return self.name


class Fn(Term):
    """Function application f(t1, ..., tn). Under Herbrand semantics a ground
    function term denotes itself."""

    __slots__ = ("name", "args", "ground", "_hash")

    def __init__(self, name: str, args):
        self.name = name
        self.args = tuple(args)
        self.ground = all(_term_is_ground(a) for a in self.args)
        self._hash = hash(("fn", name, self.args))

    def __eq__(self, other):
        return (
            other.__class__ is Fn
            and other._hash == self._hash
            and other.name == self.name
            and other.args == self.args
        )

    def __hash__(self):
        return self._hash

    def __lt__(self, other):
        return str(self) < str(other)

    def __repr__(self):
        return f"Fn({self.name!r}, {self.args!r})"

    def __str__(self):
        return f"{self.name}({', '.join(str(a) for a in self.args)})"


def _term_is_ground(t: Term) -> bool:
    cls = t.__class__
    if cls is Const:
        return True
    if cls is Var:
        return False
    return t.ground


# --------------------------------------------------------------------------
# Formulas
# --------------------------------------------------------------------------


class Formula:
    __slots__ = ()

    def __str__(self):
        return to_text(self)

    def pretty(self) -> str:
        """Render with the usual logical symbols (∀ ∃ ¬ ∧ ∨ → ↔)."""
        return to_text(self, unicode=True)


class Atom(Formula):
    """P(t1, ..., tn); a 0-ary atom is a propositional symbol."""

    __slots__ = ("pred", "args", "ground", "_hash")

    def __init__(self, pred: str, args=()):
        self.pred = pred
        self.args = tuple(args)
        self.ground = all(_term_is_ground(a) for a in self.args)
        self._hash = hash((pred, self.args))

    def __eq__(self, other):
        return (
            other.__class__ is Atom
            and other._hash == self._hash
            and other.pred == self.pred
            and other.args == self.args
        )

    def __hash__(self):
        return self._hash

    def __lt__(self, other):
        return (self.pred, [str(a) for a in self.args]) < (
            other.pred,
            [str(a) for a in other.args],
        )

    def __repr__(self):
        return f"Atom({self.pred!r}, {self.args!r})"

    @property
    def arity(self) -> int:
        return len(self.args)


@dataclass(frozen=True, eq=True)
class Equals(Formula):
    left: Term
    right: Term


@dataclass(frozen=True, eq=True)
class Not(Formula):
    body: Formula


@dataclass(frozen=True, eq=True)
class And(Formula):
    parts: tuple


@dataclass(frozen=True, eq=True)
class Or(Formula):
    parts: tuple


@dataclass(frozen=True, eq=True)
class Implies(Formula):
    left: Formula
    right: Formula


@dataclass(frozen=True, eq=True)
class Iff(Formula):
    left: Formula
    right: Formula


@dataclass(frozen=True, eq=True)
class Forall(Formula):
    vars: tuple
    body: Formula


@dataclass(frozen=True, eq=True)
class Exists(Formula):
    vars: tuple
    body: Formula


TRUE = And(())
FALSE = Or(())

Substitution = Mapping[Var, Term]


def conj(parts) -> Formula:
    """Build a flattened conjunction."""
    flat = []
    for p in parts:
        if p.__class__ is And:
            flat.extend(p.parts)
        else:
            flat.append(p)
    if len(flat) == 1:
        return flat[0]
    return And(tuple(flat))


def disj(parts) -> Formula:
    flat = []
    for p in parts:
        if p.__class__ is Or:
            flat.extend(p.parts)
        else:
            flat.append(p)
    if len(flat) == 1:
        return flat[0]
    return Or(tuple(flat))


def conjuncts(f: Formula) -> list:
    """Top-level conjuncts of a formula (flattening nested conjunctions)."""
    if f.__class__ is And:
        out = []
        for p in f.parts:
            out.extend(conjuncts(p))
        return out
    return [f]


# --------------------------------------------------------------------------
# Variables and substitution
# --------------------------------------------------------------------------


def free_vars(x) -> frozenset:
    cls = x.__class__
    if cls is Var:
        return frozenset((x,))
    if cls is Const:
        return frozenset()
    if cls is Fn or cls is Atom:
        if x.ground:
            return frozenset()
        out = set()
        for a in x.args:
            out |= free_vars(a)
        return frozenset(out)
    if cls is Equals:
        return free_vars(x.left) | free_vars(x.right)
    if cls is Not:
        return free_vars(x.body)
    if cls is And or cls is Or:
        out = set()
        for p in x.parts:
            out |= free_vars(p)
        return frozenset(out)
    if cls is Implies or cls is Iff:
        return free_vars(x.left) | free_vars(x.right)
    if cls is Forall or cls is Exists:
        return free_vars(x.body) - frozenset(x.vars)
    raise TypeError(f"not a term or formula: {x!r}")


def substitute(x, theta: Substitution):
    """Apply a substitution, resolving binding chains (x -> y -> A)."""
    if not theta:
        return x
    cls = x.__class__
    if cls is Var:
        seen = 0
        while x.__class__ is Var and x in theta:
            x = theta[x]
            seen += 1
            if seen > 1000:
                raise ValueError("cyclic substitution")
        if x.__class__ is Fn and not x.ground:
            return substitute(x, theta)
        return x
    if cls is Const:
        return x
    if cls is Atom:
        if x.ground:
            return x
        return Atom(x.pred, [substitute(a, theta) for a in x.args])
    if cls is Fn:
        if x.ground:
            return x
        return Fn(x.name, [substitute(a, theta) for a in x.args])
    if cls is Equals:
        return Equals(substitute(x.left, theta), substitute(x.right, theta))
    if cls is Not:
        return Not(substitute(x.body, theta))
    if cls is And:
        return And(tuple(substitute(p, theta) for p in x.parts))
    if cls is Or:
        return Or(tuple(substitute(p, theta) for p in x.parts))
    if cls is Implies:
        return Implies(substitute(x.left, theta), substitute(x.right, theta))
    if cls is Iff:
        return Iff(substitute(x.left, theta), substitute(x.right, theta))
    if cls is Forall or cls is Exists:
        inner = {k: v for k, v in theta.items() if k not in x.vars}
        if not inner:
            return x
        # Avoid capturing a bound variable with a substituted value.
        incoming = set()
        for v in inner.values():
            incoming |= free_vars(v)
        new_vars = []
        renaming = {}
        for v in x.vars:
            if v in incoming:
                fresh = _fresh_var(v, incoming | free_vars(x.body))
                renaming[v] = fresh
                new_vars.append(fresh)
            else:
                new_vars.append(v)
        body = substitute(x.body, renaming) if renaming else x.body
        return cls(tuple(new_vars), substitute(body, inner))
    raise TypeError(f"not a term or formula: {x!r}")


def _fresh_var(v: Var, avoid) -> Var:
    i = 1
    while Var(f"{v.name}{i}") in avoid:
        i += 1
    return Var(f"{v.name}{i}")


def atoms_of(f: Formula) -> Iterator[Atom]:
    cls = f.__class__
    if cls is Atom:
        yield f
    elif cls is Not:
        yield from atoms_of(f.body)
    elif cls is And or cls is Or:
        for p in f.parts:
            yield from atoms_of(p)
    elif cls is Implies or cls is Iff:
        yield from atoms_of(f.left)
        yield from atoms_of(f.right)
    elif cls is Forall or cls is Exists:
        yield from atoms_of(f.body)


def predicates_of(f: Formula) -> set:
    return {a.pred for a in atoms_of(f)}


def has_quantifier(f: Formula) -> bool:
    cls = f.__class__
    if cls is Forall or cls is Exists:
        return True
    if cls is Not:
        return has_quantifier(f.body)
    if cls is And or cls is Or:
        return any(has_quantifier(p) for p in f.parts)
    if cls is Implies or cls is Iff:
        return has_quantifier(f.left) or has_quantifier(f.right)
    return False


# --------------------------------------------------------------------------
# Printing
# --------------------------------------------------------------------------

_ASCII = {"not": "~", "and": " & ", "or": " | ", "imp": " -> ", "iff": " <-> ",
          "forall": "forall ", "exists": "exists ", "neq": " != ", "eq": " = ",
          "true": "true", "false": "false"}
_UNICODE = {"not": "¬", "and": " ∧ ", "or": " ∨ ", "imp": " → ", "iff": " ↔ ",
            "forall": "∀", "exists": "∃", "neq": " ≠ ", "eq": " = ",
            "true": "⊤", "false": "⊥"}

# Higher binds tighter.
_PREC = {Iff: 1, Implies: 2, Or: 3, And: 4}


def to_text(f, unicode: bool = False) -> str:
    sym = _UNICODE if unicode else _ASCII
    return _render(f, sym, 0)


def _render(f, sym, parent_prec: int) -> str:
    cls = f.__class__
    if isinstance(f, Term):
        return str(f)
    if cls is Atom:
        if not f.args:
            return f.pred
        return f"{f.pred}({', '.join(str(a) for a in f.args)})"
    if cls is Equals:
        return f"{f.left}{sym['eq']}{f.right}"
    if cls is Not:
        if f.body.__class__ is Equals:
            return f"{f.body.left}{sym['neq']}{f.body.right}"
        return sym["not"] + _render(f.body, sym, 5)
    if cls is And and not f.parts:
        return sym["true"]
    if cls is Or and not f.parts:
        return sym["false"]
    if cls in _PREC:
        prec = _PREC[cls]
        if cls is And or cls is Or:
            op = sym["and"] if cls is And else sym["or"]
            text = op.join(_render(p, sym, prec + 1) for p in f.parts)
        else:
            op = sym["imp"] if cls is Implies else sym["iff"]
            # -> is right-associative
            text = (_render(f.left, sym, prec + 1) + op
                    + _render(f.right, sym, prec if cls is Implies else prec + 1))
        return f"({text})" if prec < parent_prec else text
    if cls is Forall or cls is Exists:
        q = sym["forall"] if cls is Forall else sym["exists"]
        names = ", ".join(str(v) for v in f.vars)
        text = f"{q}{names}: {_render(f.body, sym, 0)}"
        return f"({text})" if parent_prec > 0 else text
    raise TypeError(f"cannot render {f!r}")
