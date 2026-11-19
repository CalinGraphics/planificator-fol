"""Recursive-descent parser for FOL formulas.

Grammar (lowest precedence first; -> is right-associative, quantifier bodies
extend as far to the right as possible):

    formula  := implies ( '<->' implies )*
    implies  := or ( '->' implies )?
    or       := and ( '|' and )*
    and      := unary ( ('&' | ',') unary )*
    unary    := '~' unary
              | ('forall' | 'exists') var (',' var)* (':' | '.') formula
              | '(' formula ')'
              | 'true' | 'false'
              | term ( ('=' | '!=') term )?        -- an atom if no '='
    term     := NAME [ '(' term (',' term)* ')' ] | NUMBER

Both ASCII and Unicode connectives are accepted:
    ~ ! not ¬    & and ∧    | or ∨    -> →    <-> ↔    forall ∀    exists ∃    != ≠
"""

from __future__ import annotations

import re

from .syntax import (
    FALSE, TRUE, And, Atom, Const, Equals, Exists, Forall, Fn, Iff, Implies,
    Not, Or, Var,
)


class ParseError(Exception):
    def __init__(self, message: str, line: int | None = None, source: str | None = None):
        self.message = message
        self.line = line
        self.source = source
        where = ""
        if source:
            where += f"{source}:"
        if line is not None:
            where += f"{line}:"
        super().__init__(f"{where} {message}" if where else message)


_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><->|->|!=|[()\[\],:&|~!=.+*/\-¬∧∨→↔∀∃≠⊤⊥])
    """,
    re.VERBOSE,
)

_KEYWORDS = {
    "not": "NOT", "~": "NOT", "!": "NOT", "¬": "NOT",
    "and": "AND", "&": "AND", "∧": "AND",
    "or": "OR", "|": "OR", "∨": "OR",
    "->": "IMP", "→": "IMP", "implies": "IMP",
    "<->": "IFF", "↔": "IFF", "iff": "IFF",
    "forall": "FORALL", "∀": "FORALL",
    "exists": "EXISTS", "∃": "EXISTS",
    "!=": "NEQ", "≠": "NEQ",
    "true": "TRUE", "⊤": "TRUE",
    "false": "FALSE", "⊥": "FALSE",
}


class Token:
    __slots__ = ("kind", "text", "pos")

    def __init__(self, kind, text, pos):
        self.kind = kind
        self.text = text
        self.pos = pos

    def __repr__(self):
        return f"{self.kind}:{self.text!r}"


def tokenize(text: str, line: int | None = None, source: str | None = None) -> list[Token]:
    tokens = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ParseError(f"unexpected character {text[pos]!r}", line, source)
        kind = m.lastgroup
        value = m.group()
        pos = m.end()
        if kind == "ws":
            continue
        if kind in ("name", "op") and value in _KEYWORDS:
            tokens.append(Token(_KEYWORDS[value], value, m.start()))
        elif kind == "name":
            tokens.append(Token("NAME", value, m.start()))
        elif kind == "num":
            tokens.append(Token("NUM", value, m.start()))
        else:
            tokens.append(Token(value, value, m.start()))
    tokens.append(Token("EOF", "", len(text)))
    return tokens


def is_variable_name(name: str) -> bool:
    return name[:1].islower()


class Parser:
    def __init__(self, text: str, line: int | None = None, source: str | None = None):
        self.text = text
        self.line = line
        self.source = source
        self.tokens = tokenize(text, line, source)
        self.i = 0

    # -- token helpers -------------------------------------------------------

    @property
    def peek(self) -> Token:
        return self.tokens[self.i]

    def accept(self, kind: str) -> Token | None:
        tok = self.tokens[self.i]
        if tok.kind == kind:
            self.i += 1
            return tok
        return None

    def expect(self, kind: str, what: str | None = None) -> Token:
        tok = self.accept(kind)
        if tok is None:
            found = self.peek.text or "end of input"
            self.error(f"expected {what or repr(kind)}, found {found!r}")
        return tok

    def error(self, message: str):
        raise ParseError(f"{message} in: {self.text.strip()}", self.line, self.source)

    def at_end(self) -> bool:
        return self.peek.kind == "EOF"

    def finish(self):
        if not self.at_end():
            self.error(f"unexpected {self.peek.text!r}")

    # -- formulas ------------------------------------------------------------

    def formula(self):
        left = self._implies()
        while self.accept("IFF"):
            left = Iff(left, self._implies())
        return left

    def _implies(self):
        left = self._or()
        if self.accept("IMP"):
            return Implies(left, self._implies())
        return left

    def _or(self):
        parts = [self._and()]
        while self.accept("OR"):
            parts.append(self._and())
        return parts[0] if len(parts) == 1 else Or(tuple(parts))

    def _and(self):
        parts = [self._unary()]
        while self.accept("AND") or self.accept(","):
            parts.append(self._unary())
        if len(parts) == 1:
            return parts[0]
        flat = []
        for p in parts:
            flat.extend(p.parts if p.__class__ is And and p.parts else [p])
        return And(tuple(flat))

    def _unary(self):
        if self.accept("NOT"):
            return Not(self._unary())
        tok = self.accept("FORALL") or self.accept("EXISTS")
        if tok:
            variables = [self._variable()]
            while self.accept(","):
                variables.append(self._variable())
            if not (self.accept(":") or self.accept(".")):
                self.error("expected ':' after quantified variables")
            body = self.formula()
            cls = Forall if tok.kind == "FORALL" else Exists
            return cls(tuple(variables), body)
        if self.accept("("):
            f = self.formula()
            self.expect(")", "')'")
            return f
        if self.accept("TRUE"):
            return TRUE
        if self.accept("FALSE"):
            return FALSE
        return self._atomic()

    def _variable(self) -> Var:
        tok = self.expect("NAME", "a variable")
        if not is_variable_name(tok.text):
            self.error(f"{tok.text!r} is not a variable (variables start with a lowercase letter)")
        return Var(tok.text)

    def _atomic(self):
        term = self.term()
        if self.accept("="):
            return Equals(term, self.term())
        if self.accept("NEQ"):
            return Not(Equals(term, self.term()))
        if term.__class__ is Fn:
            return Atom(term.name, term.args)
        if term.__class__ is Const and not term.name[:1].isdigit():
            return Atom(term.name, ())
        self.error(f"{term} is a term, not a formula")

    # -- terms ---------------------------------------------------------------

    def term(self):
        num = self.accept("NUM")
        if num:
            return Const(num.text)
        tok = self.expect("NAME", "a term")
        if self.accept("("):
            args = []
            if not self.accept(")"):
                args.append(self.term())
                while self.accept(","):
                    args.append(self.term())
                self.expect(")", "')'")
            return Fn(tok.text, args)
        if is_variable_name(tok.text):
            return Var(tok.text)
        return Const(tok.text)


def parse_formula(text: str, line: int | None = None, source: str | None = None):
    p = Parser(text, line, source)
    if p.at_end():
        p.error("empty formula")
    f = p.formula()
    p.finish()
    return f


def parse_term(text: str):
    p = Parser(text)
    t = p.term()
    p.finish()
    return t


def parse_atom(text: str, line: int | None = None, source: str | None = None) -> Atom:
    f = parse_formula(text, line, source)
    if f.__class__ is not Atom:
        raise ParseError(f"expected an atom, got {text.strip()!r}", line, source)
    return f
