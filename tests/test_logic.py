import random
import unittest

from planner.logic.parser import ParseError, parse_atom, parse_formula, parse_term
from planner.logic.reasoning import (
    FactIndex, LogicError, Model, Rule, answer_query, counterexample, forward_chain, holds,
)
from planner.logic.syntax import (
    And, Atom, Const, Forall, Fn, Iff, Implies, Var, free_vars,
    substitute, to_text,
)
from planner.logic.unify import match, resolve, unify

A, B, C, D = (Const(n) for n in "ABCD")
x, y, z = Var("x"), Var("y"), Var("z")


def mgu(s, t):
    theta = unify(parse_term(s), parse_term(t))
    return None if theta is None else {str(k): to_text(v) for k, v in resolve(theta).items()}


class TestUnification(unittest.TestCase):
    """Cases from AIMA 4th ed., section 9.2.2, plus edge cases."""

    def test_aima_examples(self):
        self.assertEqual(mgu("Knows(John, x)", "Knows(John, Jane)"), {"x": "Jane"})
        self.assertEqual(mgu("Knows(John, x)", "Knows(y, Bill)"), {"x": "Bill", "y": "John"})
        self.assertEqual(mgu("Knows(John, x)", "Knows(y, Mother(y))"),
                         {"x": "Mother(John)", "y": "John"})
        self.assertIsNone(mgu("Knows(John, x)", "Knows(x, Elizabeth)"))

    def test_standardizing_apart_fixes_the_last_case(self):
        self.assertEqual(mgu("Knows(John, x)", "Knows(z, Elizabeth)"),
                         {"x": "Elizabeth", "z": "John"})

    def test_occurs_check(self):
        self.assertIsNone(mgu("P(x)", "P(f(x))"))
        self.assertIsNone(mgu("P(x, x)", "P(y, f(y))"))

    def test_variable_chains(self):
        self.assertEqual(mgu("P(x, y, z)", "P(y, z, A)"), {"x": "A", "y": "A", "z": "A"})

    def test_mismatch(self):
        self.assertIsNone(mgu("P(A)", "Q(A)"))
        self.assertIsNone(mgu("P(A, B)", "P(A)"))
        self.assertIsNone(mgu("f(A)", "g(A)"))

    def test_nested_functions(self):
        self.assertEqual(mgu("P(f(x, g(y)), y)", "P(f(A, z), B)"),
                         {"x": "A", "y": "B", "z": "g(B)"})

    def test_match_agrees_with_unify_on_ground_facts(self):
        rng = random.Random(7)
        consts = [A, B, C]
        variables = [x, y, z]
        for _ in range(2000):
            n = rng.randint(1, 3)
            pattern = Atom("P", [rng.choice(consts + variables) for _ in range(n)])
            fact = Atom("P", [rng.choice(consts) for _ in range(n)])
            theta = {}
            if rng.random() < 0.5:
                theta = {x: rng.choice(consts)}
            u = unify(pattern, fact, dict(theta))
            m = match(pattern, fact, dict(theta))
            self.assertEqual(u is None, m is None, (pattern, fact, theta))
            if u is not None:
                self.assertEqual(resolve(u), m)


class TestParser(unittest.TestCase):
    def test_precedence(self):
        f = parse_formula("P & Q | R -> S <-> T")
        # <-> binds weakest, then ->, then |, then &
        self.assertIsInstance(f, Iff)
        self.assertIsInstance(f.left, Implies)
        self.assertEqual(to_text(f.left.left), "P & Q | R")
        self.assertEqual(to_text(parse_formula("(P | Q) & R")), "(P | Q) & R")

    def test_implication_is_right_associative(self):
        self.assertEqual(parse_formula("P -> Q -> R"),
                         Implies(Atom("P"), Implies(Atom("Q"), Atom("R"))))

    def test_quantifier_scope_extends_right(self):
        f = parse_formula("forall x: P(x) & Q(x)")
        self.assertEqual(f, Forall((x,), And((Atom("P", [x]), Atom("Q", [x])))))

    def test_unicode_and_ascii_agree(self):
        self.assertEqual(parse_formula("∀x: P(x) → ¬Q(x) ∨ x ≠ A"),
                         parse_formula("forall x: P(x) -> ~Q(x) | x != A"))

    def test_comma_is_conjunction(self):
        self.assertEqual(parse_formula("P(A), Q(B)"), parse_formula("P(A) & Q(B)"))

    def test_variables_and_constants(self):
        a = parse_atom("On(x, Table)")
        self.assertEqual(a.args, (x, Const("Table")))
        self.assertIsInstance(parse_term("f(x, A)"), Fn)

    def test_round_trip(self):
        for text in ["forall x, y: On(x, y) -> ~Clear(y)",
                     "exists x: P(x) & (Q(x) | R(x))",
                     "~(exists x: P(x))",
                     "x != y & (P <-> Q)"]:
            f = parse_formula(text)
            self.assertEqual(parse_formula(to_text(f)), f, text)
            self.assertEqual(parse_formula(f.pretty()), f, text)

    def test_errors(self):
        for bad in ["P(x", "forall X: P(X)", "x", "P & ", "P(x) Q(x)", "3"]:
            with self.assertRaises(ParseError, msg=bad):
                parse_formula(bad)


class TestSubstitution(unittest.TestCase):
    def test_bound_variables_are_not_replaced(self):
        f = parse_formula("P(x) & (forall x: Q(x))")
        g = substitute(f, {x: A})
        self.assertEqual(g, parse_formula("P(A) & (forall x: Q(x))"))

    def test_capture_avoidance(self):
        f = parse_formula("forall y: R(x, y)")
        g = substitute(f, {x: y})
        self.assertEqual(free_vars(g), frozenset({y}))

    def test_free_vars(self):
        self.assertEqual(free_vars(parse_formula("exists y: On(x, y)")), frozenset({x}))


def blocks_model(*facts):
    idx = FactIndex(parse_atom(f) for f in facts)
    return Model([idx], [A, B, C, D])


class TestModelChecking(unittest.TestCase):
    def setUp(self):
        self.m = blocks_model("On(A, B)", "On(B, C)", "OnTable(C)", "OnTable(D)",
                              "Clear(A)", "Clear(D)")

    def check(self, text):
        return holds(parse_formula(text), self.m)

    def test_closed_world(self):
        self.assertTrue(self.check("On(A, B)"))
        self.assertFalse(self.check("On(B, A)"))
        self.assertTrue(self.check("~On(B, A)"))

    def test_quantifiers(self):
        self.assertTrue(self.check("forall x, y: On(x, y) -> ~Clear(y)"))
        self.assertTrue(self.check("exists x: OnTable(x) & Clear(x)"))
        self.assertFalse(self.check("forall x: OnTable(x)"))
        self.assertTrue(self.check("forall x: OnTable(x) | (exists y: On(x, y))"))

    def test_counterexample(self):
        f = parse_formula("forall x: Clear(x) -> OnTable(x)")
        self.assertEqual(counterexample(f, self.m), {x: A})

    def test_queries(self):
        answers = answer_query(parse_formula("On(x, y) & ~OnTable(y)"), self.m)
        self.assertEqual(answers, [{x: A, y: B}])
        answers = answer_query(parse_formula("exists y: On(y, x)"), self.m)
        self.assertEqual(sorted(a[x].name for a in answers), ["B", "C"])

    def test_equality(self):
        self.assertTrue(self.check("exists x, y: OnTable(x) & OnTable(y) & x != y"))
        self.assertFalse(self.check("exists x, y: Clear(x) & Clear(y) & x != y & OnTable(x) & OnTable(y)"))


class TestForwardChaining(unittest.TestCase):
    def rules(self, *texts):
        return [Rule.from_formula(parse_formula(t)) for t in texts]

    def test_transitive_closure(self):
        m = blocks_model("On(A, B)", "On(B, C)", "On(C, D)")
        derived = forward_chain(self.rules("On(x, y) -> Above(x, y)",
                                           "On(x, y) & Above(y, z) -> Above(x, z)"), m)
        above = sorted(to_text(a) for a in derived)
        self.assertEqual(above, ["Above(A, B)", "Above(A, C)", "Above(A, D)",
                                 "Above(B, C)", "Above(B, D)", "Above(C, D)"])

    def test_matches_naive_fixpoint(self):
        rng = random.Random(3)
        objs = [Const(f"N{i}") for i in range(8)]
        edges = {Atom("Edge", (rng.choice(objs), rng.choice(objs))) for _ in range(14)}
        m = Model([FactIndex(edges)], objs)
        derived = {a for a in forward_chain(self.rules(
            "Edge(x, y) -> Path(x, y)", "Path(x, y) & Edge(y, z) -> Path(x, z)"), m)}
        # naive closure for comparison
        path = {(e.args[0], e.args[1]) for e in edges}
        while True:
            new = {(a, d) for (a, b) in path for e in edges
                   for (c, d) in [(e.args[0], e.args[1])] if b == c} - path
            if not new:
                break
            path |= new
        self.assertEqual(derived, {Atom("Path", p) for p in path})

    def test_negation_on_base_predicates(self):
        m = blocks_model("Room(A)", "Room(B)", "Locked(B)")
        derived = forward_chain(self.rules("Room(x) & ~Locked(x) -> Open(x)"), m)
        self.assertEqual({to_text(a) for a in derived}, {"Open(A)"})

    def test_unsafe_rules_are_rejected(self):
        for bad in ["P(x) -> Q(x, y)", "~P(x) -> Q(x)", "P(x) | R(x) -> Q(x)"]:
            with self.assertRaises(LogicError, msg=bad):
                Rule.from_formula(parse_formula(bad))


if __name__ == "__main__":
    unittest.main()
