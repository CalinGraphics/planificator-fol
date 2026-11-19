"""Model Context Protocol server (stdio transport, JSON-RPC 2.0).

Exposes the planner as tools, so an MCP client (Claude Code, Claude Desktop,
MCP Inspector, ...) can solve planning problems, validate plans and query
the knowledge base. Implemented with the standard library only.

Run:   python -m planner.mcp_server        (or: python -m planner mcp)

Every tool takes `domain` and `problem`, each of which can be
  * the name of a bundled example: domain "blocks", problem "sussman";
  * a path to a .fol file;
  * the full text of a .fol file.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from . import __version__
from .logic.parser import ParseError, parse_formula, parse_term
from .logic.reasoning import LogicError, answer_query
from .logic.syntax import free_vars, to_text
from .logic.unify import resolve, unify
from .planning.domain import PlanningError, parse_domain, parse_problem
from .planning.heuristics import HEURISTICS
from .planning.report import format_task, format_trace, result_to_dict
from .planning.search import ALGORITHMS, Searcher
from .planning.task import PlanningTask
from .planning.validate import parse_plan, validate_plan

SERVER_NAME = "fol_planner_mcp"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "domains"

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False,
              "idempotentHint": True, "openWorldHint": False}

_SOURCE_DESC = ("Either a bundled example name, a path to a .fol file, or the full .fol text. "
                "Bundled domains: see planner_list_examples.")


def _task_props():
    return {
        "domain": {"type": "string", "description": "Domain. " + _SOURCE_DESC +
                   " Example: \"blocks\"."},
        "problem": {"type": "string", "description": "Problem. " + _SOURCE_DESC +
                    " Example: \"sussman\" (bundled problems are looked up in the domain's folder)."},
    }


TOOLS = [
    {
        "name": "planner_list_examples",
        "title": "List bundled planning examples",
        "description": "List the bundled example domains and their problem files. "
                       "Use the names with the other planner_* tools.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": _READ_ONLY,
    },
    {
        "name": "planner_describe_task",
        "title": "Describe a domain and problem",
        "description": "Parse a domain and problem written in the FOL planning language and "
                       "describe them: predicates (static / fluent / derived), Horn rules, axioms, "
                       "actions, goal, number of ground actions, and whether the initial state "
                       "satisfies every axiom.",
        "inputSchema": {"type": "object", "properties": _task_props(),
                        "required": ["domain", "problem"], "additionalProperties": False},
        "annotations": _READ_ONLY,
    },
    {
        "name": "planner_solve",
        "title": "Find a plan",
        "description": "Search for a sequence of actions that reaches the goal. Returns the plan, "
                       "its cost, search statistics and an independent validation of the plan. "
                       "Use astar with hmax (or ucs) for cost-optimal plans; astar/greedy with hff "
                       "for larger problems.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **_task_props(),
                "algorithm": {"type": "string", "enum": list(ALGORITHMS), "default": "astar"},
                "heuristic": {"type": "string", "enum": list(HEURISTICS), "default": "hff",
                              "description": "Used by greedy, astar and wastar."},
                "weight": {"type": "number", "minimum": 1, "default": 2,
                           "description": "Weight w for wastar (f = g + w*h)."},
                "max_expansions": {"type": "integer", "minimum": 1, "default": 200000},
                "timeout_seconds": {"type": "number", "minimum": 0.1, "default": 60},
                "include_trace": {"type": "boolean", "default": False,
                                  "description": "Also return the facts added/deleted by each step."},
            },
            "required": ["domain", "problem"],
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "planner_validate_plan",
        "title": "Validate a plan",
        "description": "Check a plan step by step against the domain: every precondition must hold, "
                       "every axiom and constraint must hold after each step, and the goal must hold "
                       "at the end. Reports the first failing step and the false conditions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **_task_props(),
                "plan": {"type": "string",
                         "description": "One action per line, e.g. \"Unstack(C, A)\\nPutDown(C)\"."},
            },
            "required": ["domain", "problem", "plan"],
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "planner_query",
        "title": "Query the knowledge base",
        "description": "Evaluate a first-order query in the initial state (or after a plan). "
                       "Free variables (lowercase) are answered with every binding that makes the "
                       "query true; closed formulas return true/false. Derived predicates are "
                       "computed by forward chaining. Example: \"Above(x, A)\".",
        "inputSchema": {
            "type": "object",
            "properties": {
                **_task_props(),
                "query": {"type": "string", "description": "FOL formula, e.g. \"exists y: On(x, y)\"."},
                "after_plan": {"type": "string",
                               "description": "Optional plan; the query is evaluated in the state it reaches."},
                "limit": {"type": "integer", "minimum": 1, "default": 50},
            },
            "required": ["domain", "problem", "query"],
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "planner_unify",
        "title": "Unify two terms",
        "description": "Compute the most general unifier of two first-order terms or atoms "
                       "(Robinson's algorithm with occurs check). Variables are lowercase. "
                       "Example: x = \"Knows(John, y)\", y = \"Knows(z, Mother(z))\".",
        "inputSchema": {
            "type": "object",
            "properties": {"x": {"type": "string"}, "y": {"type": "string"}},
            "required": ["x", "y"],
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY,
    },
]


class ToolError(Exception):
    pass


# --------------------------------------------------------------------------
# Loading domains and problems from names, paths or text
# --------------------------------------------------------------------------


def _read_source(value: str, kind: str, domain_dir: Path | None = None):
    if "\n" in value or value.lstrip().startswith(("domain ", "problem ")):
        return value, f"<inline {kind}>", None
    path = Path(value)
    if path.suffix == ".fol" and path.is_file():
        return path.read_text(encoding="utf-8"), str(path), path.parent
    if kind == "domain":
        candidate = EXAMPLES_DIR / value / "domain.fol"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8"), str(candidate), candidate.parent
    elif domain_dir is not None:
        candidate = domain_dir / f"{value.removesuffix('.fol')}.fol"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8"), str(candidate), None
    raise ToolError(f"cannot find {kind} {value!r}. Pass a bundled name (see planner_list_examples), "
                    f"a path to a .fol file, or the .fol text itself.")


def _load_task(args) -> PlanningTask:
    for key in ("domain", "problem"):
        if not isinstance(args.get(key), str) or not args[key].strip():
            raise ToolError(f"'{key}' is required")
    d_text, d_src, d_dir = _read_source(args["domain"], "domain")
    p_text, p_src, _ = _read_source(args["problem"], "problem", d_dir)
    return PlanningTask(parse_domain(d_text, d_src), parse_problem(p_text, p_src))


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


def tool_list_examples(args):
    out = {}
    for d in sorted(EXAMPLES_DIR.iterdir()) if EXAMPLES_DIR.is_dir() else []:
        if (d / "domain.fol").is_file():
            out[d.name] = sorted(p.stem for p in d.glob("*.fol") if p.name != "domain.fol")
    lines = [f"{name}: {', '.join(probs)}" for name, probs in out.items()]
    return "\n".join(lines) or "no bundled examples found", {"domains": out}


def tool_describe(args):
    task = _load_task(args)
    return format_task(task, unicode=True), {
        "domain": task.domain.name, "problem": task.problem.name,
        "ground_actions": len(task.ground_actions),
        "initial_state_consistent": not task.violated_axioms(task.init),
    }


def tool_solve(args):
    task = _load_task(args)
    algorithm = args.get("algorithm", "astar")
    heuristic = args.get("heuristic", "hff")
    if algorithm not in ALGORITHMS:
        raise ToolError(f"algorithm must be one of {', '.join(ALGORITHMS)}")
    if heuristic not in HEURISTICS:
        raise ToolError(f"heuristic must be one of {', '.join(HEURISTICS)}")
    searcher = Searcher(task, "grounded", int(args.get("max_expansions", 200_000)),
                        float(args.get("timeout_seconds", 60)))
    result = searcher.run(algorithm, heuristic, float(args.get("weight", 2.0)))
    report = None
    if result.solved:
        report = validate_plan(task, [(a.schema.name, tuple(str(x) for x in a.args))
                                      for a in result.plan])
    data = result_to_dict(task, result, "grounded", report)
    if result.solved:
        text = (f"Plan with {len(result.plan)} steps, cost {data['cost']:g}:\n"
                + "\n".join(f"{i}. {a}" for i, a in enumerate(data["plan"], 1))
                + f"\n\nValidation: {report.message}")
        if args.get("include_trace"):
            text += "\n\nTrace:\n" + format_trace(report)
    elif result.status == "inconsistent":
        text = "Inconsistent knowledge base: " + result.note
    elif result.status == "unsolvable":
        text = "No plan exists (search space exhausted or goal unreachable in the relaxation)."
    else:
        text = (f"Search stopped ({result.status}) after {result.stats.expanded} expansions. "
                f"Try a greedy search with hff, or raise max_expansions / timeout_seconds.")
    s = data["stats"]
    text += (f"\n\nSearch: {algorithm}" + (f" with {result.heuristic}" if result.heuristic else "")
             + f"; expanded {s['expanded']}, generated {s['generated']}, {s['seconds']} s")
    return text, data


def tool_validate(args):
    task = _load_task(args)
    plan_text = args.get("plan")
    if not isinstance(plan_text, str):
        raise ToolError("'plan' is required: one action per line")
    report = validate_plan(task, parse_plan(plan_text))
    text = ("VALID: " if report.valid else "INVALID: ") + report.message
    text += "\n\n" + format_trace(report) if report.steps else ""
    return text, {"valid": report.valid, "message": report.message, "cost": report.cost,
                  "steps_checked": len(report.steps)}


def tool_query(args):
    task = _load_task(args)
    state = task.init
    if args.get("after_plan"):
        steps = parse_plan(args["after_plan"])
        report = validate_plan(task, steps)
        if len(report.steps) != len(steps) or not all(s.ok for s in report.steps):
            raise ToolError("after_plan is not executable: " + report.message)
        state = report.final_state
    query = parse_formula(args.get("query", ""))
    limit = int(args.get("limit", 50))
    answers = answer_query(query, task.model(state))
    fv = sorted(free_vars(query), key=lambda v: v.name)
    if not fv:
        value = bool(answers)
        return f"{to_text(query)} is {'TRUE' if value else 'FALSE'}", {"holds": value}
    rows = [{v.name: str(theta[v]) for v in fv} for theta in answers]
    shown = rows[:limit]
    lines = [", ".join(f"{k} = {v}" for k, v in r.items()) for r in shown]
    text = f"{len(rows)} answer(s) for {to_text(query)}" + (":\n" + "\n".join(lines) if lines else "")
    if len(rows) > limit:
        text += f"\n... {len(rows) - limit} more (raise 'limit')"
    return text, {"total_count": len(rows), "answers": shown, "has_more": len(rows) > limit}


def tool_unify(args):
    x, y = parse_term(args.get("x", "")), parse_term(args.get("y", ""))
    theta = unify(x, y)
    if theta is None:
        return f"{x} and {y} do not unify", {"unifiable": False}
    theta = resolve(theta)
    mgu = {v.name: to_text(t) for v, t in sorted(theta.items(), key=lambda kv: kv[0].name)}
    pairs = ", ".join(f"{k}/{v}" for k, v in mgu.items())
    return f"MGU = {{{pairs}}}", {"unifiable": True, "mgu": mgu}


HANDLERS = {
    "planner_list_examples": tool_list_examples,
    "planner_describe_task": tool_describe,
    "planner_solve": tool_solve,
    "planner_validate_plan": tool_validate,
    "planner_query": tool_query,
    "planner_unify": tool_unify,
}


# --------------------------------------------------------------------------
# JSON-RPC plumbing
# --------------------------------------------------------------------------


def handle(message: dict):
    """Process one JSON-RPC message; return the response dict or None."""
    method = message.get("method")
    msg_id = message.get("id")
    is_request = "id" in message
    params = message.get("params") or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, text):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return ok({
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": "Planner for domains written in first-order logic. Start with "
                            "planner_list_examples, then planner_solve.",
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return err(-32602, f"unknown tool {name!r}")
        try:
            text, data = handler(params.get("arguments") or {})
            return ok({"content": [{"type": "text", "text": text}],
                       "structuredContent": data, "isError": False})
        except (ToolError, ParseError, PlanningError, LogicError, ValueError) as e:
            return ok({"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
        except Exception as e:  # report, keep serving
            traceback.print_exc(file=sys.stderr)
            return ok({"content": [{"type": "text", "text": f"Internal error: {e}"}],
                       "isError": True})
    if not is_request:
        return None              # notifications (e.g. notifications/initialized)
    return err(-32601, f"method not found: {method}")


def serve(stdin=None, stdout=None):
    if stdin is None:
        stdin, stdout = sys.stdin, sys.stdout
        for stream in (stdin, stdout):
            try:
                stream.reconfigure(encoding="utf-8")
            except (AttributeError, ValueError):
                pass
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "parse error"}}
        else:
            if isinstance(message, list):
                responses = [r for r in (handle(m) for m in message) if r is not None]
                response = responses or None
            else:
                response = handle(message)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
