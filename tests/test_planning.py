import unittest
from pathlib import Path

from planner.logic.parser import ParseError
from planner.planning.domain import (
    PlanningError, load_domain, load_problem, parse_domain, parse_problem,
)
from planner.planning.heuristics import make_heuristic
from planner.planning.search import plan
from planner.planning.task import PlanningTask
from planner.planning.validate import parse_plan, validate_plan

ROOT = Path(__file__).resolve().parent.parent / "domains"


def task(domain, problem, **kw) -> PlanningTask:
    return PlanningTask(load_domain(ROOT / domain / "domain.fol"),
                        load_problem(ROOT / domain / f"{problem}.fol"), **kw)


def steps(p):
    return [(a.schema.name, tuple(str(x) for x in a.args)) for a in p]


class TestOptimalPlans(unittest.TestCase):
    """Known optimal plan lengths / costs, checked with every optimal algorithm."""

    CASES = [
        ("blocks", "sussman", 6),
        ("blocks", "reverse4", 8),
        ("blocks", "derived_goal", 4),
        ("blocks", "one_tower", 6),
        ("scheduling", "project", 37),
        ("delivery", "office", 19),
    ]

    def test_ucs_and_astar_hmax_agree(self):
        for domain, problem, cost in self.CASES:
            for alg, h in [("ucs", None), ("astar", "hmax"), ("astar", "blind")]:
                with self.subTest(problem=problem, algorithm=alg, heuristic=h):
                    t = task(domain, problem)
                    r = plan(t, alg, h)
                    self.assertTrue(r.solved)
                    self.assertEqual(r.cost, cost)
                    self.assertTrue(validate_plan(t, steps(r.plan)).valid)

    def test_bfs_and_iddfs_are_optimal_in_steps(self):
        for domain, problem in [("blocks", "sussman"), ("blocks", "reverse4")]:
            for alg in ("bfs", "iddfs"):
                with self.subTest(problem=problem, algorithm=alg):
                    r = plan(task(domain, problem), alg)
                    self.assertEqual(len(r.plan), {"sussman": 6, "reverse4": 8}[problem])

    def test_bfs_ignores_costs(self):
        # BFS minimises the number of steps (7), not the cost; UCS finds 37 < 38.
        r = plan(task("scheduling", "project"), "bfs")
        self.assertEqual(len(r.plan), 7)
        self.assertGreater(r.cost, 37)

    def test_every_algorithm_returns_a_valid_plan(self):
        configs = [("bfs", None), ("dfs", None), ("iddfs", None), ("ucs", None),
                   ("greedy", "goal_count"), ("greedy", "hff"), ("astar", "hadd"),
                   ("astar", "hff"), ("wastar", "hff")]
        for alg, h in configs:
            with self.subTest(algorithm=alg, heuristic=h):
                t = task("blocks", "sussman")
                r = plan(t, alg, h)
                self.assertTrue(r.solved)
                self.assertTrue(validate_plan(t, steps(r.plan)).valid)

    def test_large_problem_with_ff(self):
        t = task("blocks", "large8")
        r = plan(t, "greedy", "hff")
        self.assertTrue(r.solved)
        self.assertTrue(validate_plan(t, steps(r.plan)).valid)


class TestSpecialOutcomes(unittest.TestCase):
    def test_inconsistent_knowledge_base(self):
        r = plan(task("scheduling", "cyclic"), "astar", "hff")
        self.assertEqual(r.status, "inconsistent")
        self.assertIn("Precedes", r.note)

    def test_unsolvable_detected_by_relaxation(self):
        t = task("scheduling", "missing_skill")
        r = plan(t, "astar", "hmax")
        self.assertEqual(r.status, "unsolvable")
        self.assertEqual(r.stats.expanded, 0)
        self.assertEqual(plan(t, "bfs").status, "unsolvable")

    def test_constraint_is_enforced(self):
        t = task("delivery", "office")
        r = plan(t, "astar", "hff")
        self.assertGreater(r.stats.pruned_by_constraints, 0)
        # the fragile vase is never held together with something else
        state = t.init
        for a in r.plan:
            state = t.apply(state, a)
            held = [f for f in state if f.pred == "Holds"]
            if any(str(f.args[1]) == "Vase" for f in held):
                self.assertEqual(len(held), 1)

    def test_limits(self):
        r = plan(task("blocks", "large8"), "bfs", max_expansions=50)
        self.assertEqual(r.status, "limit")


class TestSuccessorModes(unittest.TestCase):
    def test_lifted_and_grounded_generate_the_same_successors(self):
        for domain, problem in [("blocks", "sussman"), ("scheduling", "project"),
                                ("delivery", "office")]:
            t = task(domain, problem)
            frontier, seen = [t.init], {t.init}
            while frontier and len(seen) < 300:
                s = frontier.pop()
                g = {a.name for a in t.applicable(s, "grounded")}
                l = {a.name for a in t.applicable(s, "lifted")}
                self.assertEqual(g, l, f"{problem}: state {sorted(map(str, s))}")
                for a in t.applicable(s):
                    c = t.apply(s, a)
                    if c not in seen:
                        seen.add(c)
                        frontier.append(c)

    def test_lifted_search(self):
        r = plan(task("blocks", "sussman"), "astar", "hff", mode="lifted")
        self.assertEqual(r.cost, 6)


class TestHeuristics(unittest.TestCase):
    def test_hmax_is_admissible_on_sussman(self):
        t = task("blocks", "sussman")
        h = make_heuristic("hmax", t)
        # optimal cost from init is 6
        self.assertLessEqual(h(t.init), 6)
        self.assertGreater(h(t.init), 0)

    def test_values_at_goal_are_zero(self):
        t = task("blocks", "sussman")
        r = plan(t, "astar", "hff")
        state = t.init
        for a in r.plan:
            state = t.apply(state, a)
        for name in ("goal_count", "hmax", "hadd", "hff"):
            self.assertEqual(make_heuristic(name, t)(state), 0, name)

    def test_relaxed_plan_is_executable_ignoring_deletes(self):
        t = task("blocks", "reverse4")
        h = make_heuristic("hff", t)
        rp = h.relaxed_plan(t.init)
        self.assertEqual(len(rp), h(t.init))


class TestValidator(unittest.TestCase):
    def setUp(self):
        self.t = task("blocks", "sussman")

    def test_detects_false_precondition(self):
        rep = validate_plan(self.t, parse_plan("PickUp(A)"))
        self.assertFalse(rep.valid)
        self.assertIn("Clear(A)", rep.steps[0].failed_conditions)

    def test_detects_unreached_goal(self):
        rep = validate_plan(self.t, parse_plan("Unstack(C, A)\nPutDown(C)"))
        self.assertFalse(rep.valid)
        self.assertIn("goal is not reached", rep.message)

    def test_rejects_unknown_actions_and_objects(self):
        self.assertFalse(validate_plan(self.t, parse_plan("Fly(A)")).valid)
        self.assertFalse(validate_plan(self.t, parse_plan("PickUp(Z)")).valid)
        self.assertFalse(validate_plan(self.t, parse_plan("Stack(A)")).valid)

    def test_plan_file_syntax(self):
        text = "# comment\n1. Unstack(C, A)\n2: PutDown(C)  ; trailing\n\n"
        self.assertEqual(parse_plan(text), [("Unstack", ("C", "A")), ("PutDown", ("C",))])


class TestDomainErrors(unittest.TestCase):
    BASE = """domain D
types: Block
predicates: On(x, y), Clear(x), Above(x, y)
derive: On(x, y) -> Above(x, y)
"""

    def bad(self, extra, exc=PlanningError):
        with self.assertRaises(exc):
            parse_domain(self.BASE + extra)

    def test_effects_on_derived_predicates(self):
        self.bad("action A(x: Block, y: Block)\n  pre: Clear(x)\n  eff: Above(x, y)\n")

    def test_unknown_predicate_and_arity(self):
        self.bad("action A(x: Block)\n  pre: Free(x)\n  eff: Clear(x)\n")
        self.bad("action A(x: Block)\n  pre: On(x)\n  eff: Clear(x)\n")

    def test_unbound_variable_in_effect(self):
        self.bad("action A(x: Block)\n  pre: Clear(x)\n  eff: On(x, y)\n")

    def test_non_literal_effect(self):
        self.bad("action A(x: Block)\n  pre: Clear(x)\n  eff: Clear(x) | On(x, x)\n", ParseError)

    def test_problem_errors(self):
        d = parse_domain(self.BASE + "action A(x: Block)\n  pre: Clear(x)\n  eff: ~Clear(x)\n")
        bad_problems = [
            "problem P\nobjects: A : Block\ninit: Clear(B)\ngoal: Clear(A)",       # unknown object
            "problem P\nobjects: A : Block\ninit: Above(A, A)\ngoal: Clear(A)",    # derived in init
            "problem P\nobjects: A : Block\ninit: ~Clear(A)\ngoal: Clear(A)",      # negative init
        ]
        for text in bad_problems:
            with self.assertRaises((PlanningError, ParseError), msg=text):
                PlanningTask(d, parse_problem(text))


if __name__ == "__main__":
    unittest.main()
