"""Heuristics for informed search.

All informed heuristics except goal-count are computed on the *delete
relaxation* of the task: delete effects are ignored and negative conditions
are assumed achievable for free. Horn rules become zero-cost relaxed actions.

* blind       h = 0                                                (admissible)
* goal_count  number of unsatisfied top-level goal conjuncts       (not admissible)
* hmax        cost of the most expensive goal atom in the relaxation (admissible)
* hadd        sum of relaxed atom costs (Bonet & Geffner, 2001)   (not admissible)
* hff         cost of an explicit relaxed plan (Hoffmann & Nebel, 2001) (not admissible)

Preconditions and goals may be arbitrary (expanded, quantifier-free)
formulas, so relaxed costs are defined on formulas in negation normal form:

    cost(atom) = cost of the atom          cost(~atom) = 0
    cost(A & B) = cost(A) + cost(B)        (max instead of + for hmax)
    cost(A | B) = min(cost(A), cost(B))

Atom costs are computed with a generalised Dijkstra algorithm (Knuth, 1977).
Operators whose precondition is a plain conjunction use the classic counter
technique; the rest are re-evaluated whenever one of their atoms is settled.
"""

from __future__ import annotations

import heapq
from math import inf

from ..logic.reasoning import holds
from ..logic.syntax import FALSE, TRUE, And, Atom, Iff, Implies, Not, Or, conjuncts
from .task import PlanningTask

HEURISTICS = ("blind", "goal_count", "hmax", "hadd", "hff")
ADMISSIBLE = {"blind": True, "goal_count": False, "hmax": True, "hadd": False, "hff": False}


def nnf(f, negate: bool = False):
    """Negation normal form of a quantifier-free formula."""
    cls = f.__class__
    if cls is Atom:
        return Not(f) if negate else f
    if cls is Not:
        return nnf(f.body, not negate)
    if cls is And:
        if not f.parts:
            return FALSE if negate else TRUE
        parts = tuple(nnf(p, negate) for p in f.parts)
        return Or(parts) if negate else And(parts)
    if cls is Or:
        if not f.parts:
            return TRUE if negate else FALSE
        parts = tuple(nnf(p, negate) for p in f.parts)
        return And(parts) if negate else Or(parts)
    if cls is Implies:
        return nnf(Or((Not(f.left), f.right)), negate)
    if cls is Iff:
        both = Or((And((f.left, f.right)), And((Not(f.left), Not(f.right)))))
        return nnf(both, negate)
    raise TypeError(f"nnf expects a quantifier-free formula, got {f!r}")


# Compiled formulas: an int (atom id), None (always true: a negated atom),
# ('&', children) or ('|', children).

def _compile(f, ids: dict):
    cls = f.__class__
    if cls is Atom:
        i = ids.get(f)
        if i is None:
            i = ids[f] = len(ids)
        return i
    if cls is Not:
        return None
    parts = tuple(_compile(p, ids) for p in f.parts)
    if cls is And:
        parts = tuple(p for p in parts if p is not None)
        if len(parts) == 1:
            return parts[0]
        return ("&", parts) if parts else None
    if any(p is None for p in parts):
        return None                      # a disjunct that is free makes the whole thing free
    if not parts:
        return ("|", ())                 # false
    return parts[0] if len(parts) == 1 else ("|", parts)


def _value(c, costs, use_max: bool) -> float:
    if c is None:
        return 0.0
    if c.__class__ is int:
        return costs[c]
    kind, parts = c
    if kind == "&":
        total = 0.0
        for p in parts:
            v = _value(p, costs, use_max)
            if v == inf:
                return inf
            if use_max:
                if v > total:
                    total = v
            else:
                total += v
        return total
    best = inf
    for p in parts:
        v = _value(p, costs, use_max)
        if v < best:
            best = v
    return best


def _support(c, costs, out: list):
    """Atom ids a relaxed plan has to achieve to satisfy c (cheapest disjunct)."""
    if c is None:
        return
    if c.__class__ is int:
        out.append(c)
        return
    kind, parts = c
    if kind == "&":
        for p in parts:
            _support(p, costs, out)
    elif parts:
        _support(min(parts, key=lambda p: _value(p, costs, False)), costs, out)


def _atom_ids(c, out: set):
    if c is None:
        return
    if c.__class__ is int:
        out.add(c)
        return
    for p in c[1]:
        _atom_ids(p, out)


def _flat_conjunction(c):
    """Atom ids if c is a plain conjunction of atoms, else None."""
    if c is None:
        return ()
    if c.__class__ is int:
        return (c,)
    kind, parts = c
    if kind == "&" and all(p.__class__ is int for p in parts):
        return tuple(dict.fromkeys(parts))
    return None


class Heuristic:
    name = "blind"
    admissible = True

    def __init__(self, task: PlanningTask):
        self.task = task

    def __call__(self, state) -> float:
        return 0.0


class GoalCount(Heuristic):
    name = "goal_count"
    admissible = False

    def __init__(self, task):
        super().__init__(task)
        self.parts = conjuncts(task.simplified_goal)

    def __call__(self, state):
        m = self.task.model(state)
        return float(sum(1 for p in self.parts if not holds(p, m)))


class RelaxedHeuristic(Heuristic):
    """hmax / hadd / hff on the delete relaxation."""

    def __init__(self, task: PlanningTask, kind: str):
        super().__init__(task)
        self.kind = kind
        self.name = kind
        self.admissible = kind == "hmax"
        self.use_max = kind == "hmax"

        ids: dict = {}
        pres, adds, op_costs, labels = [], [], [], []
        for a in task.ground_actions:
            pres.append(_compile(nnf(a.pre), ids))
            adds.append(a.add)
            op_costs.append(a.cost)
            labels.append(a)
        for r in task.ground_rules:
            pres.append(_compile(nnf(r.body), ids))
            adds.append((r.head,))
            op_costs.append(0.0)
            labels.append(None)
        self.goal = _compile(nnf(task.simplified_goal), ids)
        self.adds = [tuple(ids.setdefault(a, len(ids)) for a in add) for add in adds]
        self.ids = ids
        self.atoms = [None] * len(ids)
        for atom, i in ids.items():
            self.atoms[i] = atom
        self.pres = pres
        self.op_costs = op_costs
        self.labels = labels
        n_ops = len(pres)

        # counters for conjunctive operators, re-evaluation for the others
        self.flat = [None] * n_ops
        self.watch_flat = [[] for _ in ids]
        self.watch_general = [[] for _ in ids]
        self.free_ops = []
        for i, pre in enumerate(pres):
            flat = _flat_conjunction(pre)
            if flat is not None:
                self.flat[i] = flat
                if not flat:
                    self.free_ops.append(i)
                for a in flat:
                    self.watch_flat[a].append(i)
            else:
                atoms = set()
                _atom_ids(pre, atoms)
                if not atoms:
                    self.free_ops.append(i)
                for a in atoms:
                    self.watch_general[a].append(i)
        goal_atoms = set()
        _atom_ids(self.goal, goal_atoms)
        self.goal_atoms = goal_atoms

    def explore(self, state):
        """Relaxed cost of every atom id, and the operator that achieves it."""
        ids = self.ids
        n = len(ids)
        use_max = self.use_max
        costs = [inf] * n
        supporter = [-1] * n
        flat = self.flat
        pres = self.pres
        adds = self.adds
        op_costs = self.op_costs
        remaining = [len(f) if f is not None else 0 for f in flat]
        acc = [0.0] * len(pres)
        best_general = {}
        heap = []
        push = heapq.heappush

        def achieve(op, value):
            value += op_costs[op]
            for a in adds[op]:
                if value < costs[a]:
                    costs[a] = value
                    supporter[a] = op
                    push(heap, (value, a))

        for atom in state:
            i = ids.get(atom)
            if i is not None:
                costs[i] = 0.0
                heap.append((0.0, i))
        heapq.heapify(heap)
        for op in self.free_ops:
            achieve(op, 0.0 if flat[op] is not None else _value(pres[op], costs, use_max))

        settled = [False] * n
        goal_left = len(self.goal_atoms)
        goal_atoms = self.goal_atoms
        watch_flat = self.watch_flat
        watch_general = self.watch_general
        pop = heapq.heappop
        while heap:
            value, a = pop(heap)
            if settled[a] or value > costs[a]:
                continue
            settled[a] = True
            if a in goal_atoms:
                goal_left -= 1
                if goal_left == 0:
                    break
            for op in watch_flat[a]:
                if use_max:
                    if value > acc[op]:
                        acc[op] = value
                else:
                    acc[op] += value
                remaining[op] -= 1
                if remaining[op] == 0:
                    achieve(op, acc[op])
            for op in watch_general[a]:
                v = _value(pres[op], costs, use_max)
                if v < best_general.get(op, inf):
                    best_general[op] = v
                    achieve(op, v)
        return costs, supporter

    def __call__(self, state) -> float:
        costs, supporter = self.explore(state)
        value = _value(self.goal, costs, self.use_max)
        if value == inf or self.kind != "hff":
            return value
        return float(sum(self.op_costs[op] for op in self._relaxed_plan(state, costs, supporter)))

    def _relaxed_plan(self, state, costs, supporter) -> list:
        ids = self.ids
        in_state = {ids[a] for a in state if a in ids}
        needed = []
        _support(self.goal, costs, needed)
        chosen, chosen_set, seen = [], set(), set()
        while needed:
            a = needed.pop()
            if a in seen or a in in_state:
                continue
            seen.add(a)
            op = supporter[a]
            if op < 0:
                continue
            if op not in chosen_set:
                chosen_set.add(op)
                chosen.append(op)
                _support(self.pres[op], costs, needed)
        return chosen

    def relaxed_plan(self, state) -> list:
        """The relaxed plan behind h_ff (ground actions, unordered)."""
        costs, supporter = self.explore(state)
        if _value(self.goal, costs, False) == inf:
            return []
        return [self.labels[op] for op in self._relaxed_plan(state, costs, supporter)
                if self.labels[op] is not None]


def make_heuristic(name: str, task: PlanningTask) -> Heuristic:
    if name == "blind":
        return Heuristic(task)
    if name == "goal_count":
        return GoalCount(task)
    if name in ("hmax", "hadd", "hff"):
        return RelaxedHeuristic(task, name)
    raise ValueError(f"unknown heuristic {name!r}; choose from {', '.join(HEURISTICS)}")
