"""Unification (Robinson, 1965), following AIMA 4th ed., Figure 9.1.

`unify` computes the most general unifier (MGU) of two terms or atoms and
includes the occurs check, so x and f(x) do not unify.

`match` is the special case used by the inference engine: the right-hand side
is always a ground fact, so unification reduces to one-way matching. It gives
the same answer as `unify` on that input (tests check this) but avoids the
general bookkeeping, which matters in the inner loop of the planner.
"""

from __future__ import annotations

from .syntax import Atom, Const, Fn, Term, Var, substitute


def unify(x, y, theta: dict | None = None) -> dict | None:
    """Return the MGU of x and y that extends theta, or None if none exists."""
    return _unify(x, y, {} if theta is None else theta)


def _unify(x, y, theta):
    if theta is None:
        return None
    if x == y:
        return theta
    if x.__class__ is Var:
        return _unify_var(x, y, theta)
    if y.__class__ is Var:
        return _unify_var(y, x, theta)
    if x.__class__ is Atom and y.__class__ is Atom:
        if x.pred != y.pred or len(x.args) != len(y.args):
            return None
        return _unify_args(x.args, y.args, theta)
    if x.__class__ is Fn and y.__class__ is Fn:
        if x.name != y.name or len(x.args) != len(y.args):
            return None
        return _unify_args(x.args, y.args, theta)
    return None


def _unify_args(xs, ys, theta):
    for a, b in zip(xs, ys):
        theta = _unify(a, b, theta)
        if theta is None:
            return None
    return theta


def _unify_var(var: Var, x, theta):
    if var in theta:
        return _unify(theta[var], x, theta)
    if x.__class__ is Var and x in theta:
        return _unify(var, theta[x], theta)
    if occurs(var, x, theta):
        return None
    extended = dict(theta)
    extended[var] = x
    return extended


def occurs(var: Var, x, theta) -> bool:
    """Occurs check: does var appear in x (after applying theta)?"""
    cls = x.__class__
    if cls is Var:
        if x == var:
            return True
        return x in theta and occurs(var, theta[x], theta)
    if cls is Fn or cls is Atom:
        return any(occurs(var, a, theta) for a in x.args)
    return False


def resolve(theta: dict) -> dict:
    """Fully apply a substitution to itself (idempotent form, for display)."""
    return {v: substitute(t, theta) for v, t in theta.items()}


def match(pattern: Atom, fact: Atom, theta: dict) -> dict | None:
    """Unify a (possibly non-ground) pattern with a ground fact.

    Assumes every value bound in theta is ground, which is always true inside
    the inference engine because facts are ground.
    """
    if pattern.pred != fact.pred:
        return None
    pargs = pattern.args
    fargs = fact.args
    if len(pargs) != len(fargs):
        return None
    new = None
    for p, f in zip(pargs, fargs):
        pc = p.__class__
        if pc is Var:
            current = theta if new is None else new
            bound = current.get(p)
            if bound is None:
                if new is None:
                    new = dict(theta)
                new[p] = f
            elif bound != f:
                return None
        elif pc is Const or p.ground:
            if p != f:
                return None
        else:  # function term with variables inside: fall back to full unification
            return unify(pattern, fact, theta)
    return theta if new is None else new


def term_from(value) -> Term:
    """Small helper used by tests and the CLI."""
    if isinstance(value, Term):
        return value
    return Var(value) if value[:1].islower() else Const(value)
