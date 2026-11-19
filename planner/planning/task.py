"""A planning task: the state space defined by a domain and a problem.

States are frozensets of ground *fluent* atoms. Static facts (types, static
relations and everything the rules derive from them) are computed once.
Derived fluent predicates are recomputed per state by forward chaining.

Two ways of generating successors are offered:

* lifted   - for each action schema, the precondition is solved as a
             conjunctive query against the current state (unification);
* grounded - actions are instantiated once, before search, by a relaxed
             reachability analysis (again driven by unification); static
             parts of preconditions are evaluated away and quantifiers are
             expanded over the finite set of objects.

Both produce exactly the same successors; the grounded mode is faster and is
also what the heuristics need.
"""

from __future__ import annotations

from itertools import product

from ..logic.reasoning import (
    FactIndex, Model, counterexample, forward_chain, holds, solve,
)
from ..logic.syntax import (
    FALSE, TRUE, And, Atom, Const, Equals, Exists, Forall, Formula, Iff,
    Implies, Not, Or, conjuncts, free_vars, substitute,
    to_text,
)
from .domain import Domain, PlanningError, Problem, validate_problem


class GroundAction:
    __slots__ = ("schema", "args", "pre", "add", "delete", "cost", "_hash", "name")

    def __init__(self, schema, args, pre, add, delete, cost):
        self.schema = schema
        self.args = tuple(args)
        self.pre = pre                  # quantifier-free, static parts removed
        self.add = frozenset(add)
        self.delete = frozenset(delete)
        self.cost = cost
        self.name = f"{schema.name}({', '.join(str(a) for a in self.args)})" if self.args \
            else schema.name
        self._hash = hash((schema.name, self.args))

    def __eq__(self, other):
        return (other.__class__ is GroundAction and other.schema.name == self.schema.name
                and other.args == self.args)

    def __hash__(self):
        return self._hash

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"GroundAction({self.name})"


class GroundRule:
    """A ground instance of a fluent Horn rule (used by the heuristics)."""

    __slots__ = ("head", "body")

    def __init__(self, head, body):
        self.head = head
        self.body = body


class PlanningTask:
    def __init__(self, domain: Domain, problem: Problem, check_axioms: bool = False):
        validate_problem(problem, domain)
        self.domain = domain
        self.problem = problem
        # Constraints are always enforced; invariants only on request.
        self.search_kinds = ("axiom", "constraint") if check_axioms else ("constraint",)

        objs = dict(domain.constants)
        objs.update(problem.objects)
        self.object_types = objs
        self.objects = tuple(Const(n) for n in objs)

        self.fluent_preds = domain.fluent_predicates
        self.fluent_derived_preds = domain.fluent_derived_predicates
        self.static_preds = domain.static_predicates
        self.fluent_rules = [r for r in domain.rules if r.head.pred in self.fluent_derived_preds]
        static_rules = [r for r in domain.rules if r.head.pred not in self.fluent_derived_preds]

        static_base = FactIndex()
        for name, typ in objs.items():
            if typ:
                static_base.add(Atom(typ, (Const(name),)))
        init_fluents = []
        for a in problem.init:
            if a.pred in self.fluent_preds:
                init_fluents.append(a)
            else:
                static_base.add(a)
        derived_static = forward_chain(static_rules, Model([static_base], self.objects))
        for a in derived_static:
            static_base.add(a)
        self.static_facts = static_base
        self.static_model = Model([static_base], self.objects)

        self.init = frozenset(init_fluents)
        goal = problem.goal
        fv = free_vars(goal)
        if fv:  # free variables of the goal are read existentially
            goal = Exists(tuple(sorted(fv, key=lambda v: v.name)), goal)
        self.goal = goal
        # free variables of axioms are read universally
        self.axioms = [(ax.formula if not free_vars(ax.formula)
                        else Forall(tuple(sorted(free_vars(ax.formula), key=lambda v: v.name)),
                                    ax.formula), ax.kind)
                       for ax in domain.axioms]
        self.check_in_search = any(kind in self.search_kinds for _, kind in self.axioms)
        self.values = problem.values

        self._model_cache: dict = {}
        self._ground_actions = None
        self._ground_rules = None
        self._triggers = None
        self._always = None
        self._goal_simplified = None

    # -- models --------------------------------------------------------------

    def model(self, state) -> Model:
        """Herbrand model of a state: static facts + state + derived facts."""
        m = self._model_cache.get(state)
        if m is not None:
            return m
        state_index = FactIndex(state)
        m = Model([self.static_facts, state_index], self.objects)
        if self.fluent_rules:
            derived = forward_chain(self.fluent_rules, m)
            if len(derived):
                m = m.extended(derived)
        if len(self._model_cache) > 200_000:
            self._model_cache.clear()
        self._model_cache[state] = m
        return m

    def is_goal(self, state) -> bool:
        return holds(self.simplified_goal, self.model(state))

    def violated_axioms(self, state, kinds=("axiom", "constraint")) -> list:
        """(formula, counterexample binding, kind) for every violated axiom or
        constraint of the requested kinds."""
        m = self.model(state)
        out = []
        for ax, kind in self.axioms:
            if kind not in kinds:
                continue
            if ax.__class__ is Forall:
                cex = counterexample(ax, m)
                if cex is not None:
                    out.append((ax, cex, kind))
            elif not holds(ax, m):
                out.append((ax, {}, kind))
        return out

    def apply(self, state, action: GroundAction):
        return (state - action.delete) | action.add

    # -- successor generation ------------------------------------------------

    def applicable(self, state, mode: str = "grounded"):
        if mode == "lifted":
            return list(self._applicable_lifted(state))
        return self._applicable_grounded(state)

    def _applicable_grounded(self, state):
        self.ground_actions  # make sure the action index exists
        m = self.model(state)
        seen = set()
        out = []
        candidates = list(self._always)
        triggers = self._triggers
        for atom in state:
            acts = triggers.get(atom)
            if acts:
                candidates.extend(acts)
        for layer in m.layers[2:]:          # derived fluent facts
            for atom in layer:
                acts = triggers.get(atom)
                if acts:
                    candidates.extend(acts)
        for a in candidates:
            if a in seen:
                continue
            seen.add(a)
            if holds(a.pre, m):
                out.append(a)
        out.sort(key=lambda a: a.name)
        return out

    def _applicable_lifted(self, state):
        m = self.model(state)
        for schema in self.domain.actions.values():
            for theta in solve(schema.full_precondition, m, {}, schema.params):
                args = [theta[v] for v in schema.params]
                yield self._instantiate(schema, args, theta, simplify_pre=False)

    def _instantiate(self, schema, args, theta, simplify_pre=True):
        pre = substitute(schema.pre, theta)
        if simplify_pre:
            pre = self.simplify(pre)
        add = [substitute(a, theta) for a in schema.add]
        delete = [substitute(a, theta) for a in schema.delete]
        cost = schema.cost.evaluate(theta, self.values)
        if cost < 0:
            raise PlanningError(f"negative cost for {schema.name}{tuple(map(str, args))}")
        return GroundAction(schema, args, pre, add, delete, cost)

    # -- grounding -----------------------------------------------------------

    @property
    def ground_actions(self) -> list:
        if self._ground_actions is None:
            self._ground()
        return self._ground_actions

    @property
    def ground_rules(self) -> list:
        if self._ground_rules is None:
            self._ground()
        return self._ground_rules

    def _ground(self):
        """Instantiate actions reachable in the delete relaxation.

        Starting from the initial state, every action whose positive
        precondition atoms can be matched (by unification) against the facts
        reached so far is instantiated and its add effects are added to the
        reached set, until a fixpoint. Negative preconditions are ignored here,
        so the result over-approximates the actions that can ever be applied.
        """
        reached = FactIndex(self.init)
        relaxed_rules = [r.relaxed() for r in self.fluent_rules]
        actions: dict = {}
        while True:
            base = Model([self.static_facts, reached], self.objects)
            derived = forward_chain(relaxed_rules, base)
            model = base.extended(derived)
            grew = False
            for schema in self.domain.actions.values():
                relaxed_pre = [c for c in schema.full_precondition if _positive_or_equality(c)]
                for theta in solve(relaxed_pre, model, {}, schema.params):
                    args = tuple(theta[v] for v in schema.params)
                    key = (schema.name, args)
                    if key in actions:
                        continue
                    ga = self._instantiate(schema, args, theta)
                    actions[key] = ga
                    if ga.pre == FALSE:
                        continue
                    for a in ga.add:
                        if reached.add(a):
                            grew = True
            if not grew:
                break
        self._ground_actions = sorted((a for a in actions.values() if a.pre != FALSE),
                                      key=lambda a: a.name)

        rules = []
        for rule in self.fluent_rules:
            relaxed = rule.relaxed()
            seen = set()
            for theta in solve(relaxed.body, model):
                head = substitute(rule.head, theta)
                body = self.simplify(And(tuple(substitute(l, theta) for l in rule.body)))
                key = (head, body)
                if body != FALSE and key not in seen:
                    seen.add(key)
                    rules.append(GroundRule(head, body))
        self._ground_rules = rules

        # Index: each action is triggered by one positive fluent atom of its precondition.
        triggers: dict = {}
        always = []
        for a in self._ground_actions:
            trigger = None
            for c in conjuncts(a.pre):
                if c.__class__ is Atom:
                    trigger = c
                    break
            if trigger is None:
                always.append(a)
            else:
                triggers.setdefault(trigger, []).append(a)
        self._triggers = triggers
        self._always = always

    # -- static simplification ----------------------------------------------

    @property
    def simplified_goal(self) -> Formula:
        if self._goal_simplified is None:
            self._goal_simplified = self.simplify(self.goal)
        return self._goal_simplified

    def is_static(self, pred: str) -> bool:
        return pred in self.static_preds

    def simplify(self, f: Formula) -> Formula:
        """Partially evaluate a closed formula: static atoms and equalities are
        replaced by their (fixed) truth value and quantifiers are expanded over
        the objects. The result mentions only fluent atoms."""
        cls = f.__class__
        if cls is Atom:
            if not f.ground:
                raise PlanningError(f"cannot simplify open atom {to_text(f)}")
            if f.pred in self.static_preds:
                return TRUE if f in self.static_facts else FALSE
            return f
        if cls is Equals:
            return TRUE if f.left == f.right else FALSE
        if cls is Not:
            body = self.simplify(f.body)
            if body == TRUE:
                return FALSE
            if body == FALSE:
                return TRUE
            if body.__class__ is Not:
                return body.body
            return Not(body)
        if cls is And:
            parts = []
            for p in f.parts:
                s = self.simplify(p)
                if s == FALSE:
                    return FALSE
                if s == TRUE:
                    continue
                parts.extend(s.parts if s.__class__ is And else [s])
            parts = list(dict.fromkeys(parts))
            return TRUE if not parts else parts[0] if len(parts) == 1 else And(tuple(parts))
        if cls is Or:
            parts = []
            for p in f.parts:
                s = self.simplify(p)
                if s == TRUE:
                    return TRUE
                if s == FALSE:
                    continue
                parts.extend(s.parts if s.__class__ is Or else [s])
            parts = list(dict.fromkeys(parts))
            return FALSE if not parts else parts[0] if len(parts) == 1 else Or(tuple(parts))
        if cls is Implies:
            return self.simplify(Or((Not(f.left), f.right)))
        if cls is Iff:
            left, right = self.simplify(f.left), self.simplify(f.right)
            return self.simplify(Or((And((left, right)), And((Not(left), Not(right))))))
        if cls is Forall or cls is Exists:
            instances = [substitute(f.body, theta) for theta in self._candidate_bindings(f)]
            if cls is Forall:
                return self.simplify(And(tuple(instances)))
            return self.simplify(Or(tuple(instances)))
        raise TypeError(f"not a formula: {f!r}")

    def _candidate_bindings(self, f):
        """Bindings worth expanding for a quantifier. For  forall x: G -> C  and
        exists x: G & C,  the static atoms of G restrict the bindings: any other
        binding makes the instance trivially true (forall) or false (exists)."""
        body = f.body
        if f.__class__ is Forall:
            guard = conjuncts(body.left) if body.__class__ is Implies else []
        else:
            guard = conjuncts(body)
        static_atoms = [g for g in guard if g.__class__ is Atom and g.pred in self.static_preds]
        if static_atoms:
            return list(solve(static_atoms, self.static_model, {}, f.vars))
        return [dict(zip(f.vars, combo)) for combo in product(self.objects, repeat=len(f.vars))]

    # -- information ---------------------------------------------------------

    def describe_state(self, state) -> list:
        return sorted(to_text(a) for a in state)


def _positive_or_equality(f) -> bool:
    if f.__class__ is Atom or f.__class__ is Equals:
        return True
    return f.__class__ is Not and f.body.__class__ is Equals
