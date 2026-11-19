"""End-to-end tests of the command line and of the MCP server process."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCKS = [str(ROOT / "domains/blocks/domain.fol"), str(ROOT / "domains/blocks/sussman.fol")]


def run(*args, stdin=None):
    return subprocess.run([sys.executable, "-m", "planner", *args], cwd=ROOT, input=stdin,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


class TestCLI(unittest.TestCase):
    def test_solve_writes_a_plan_that_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan.txt"
            r = run("solve", *BLOCKS, "-o", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Validation OK", r.stdout)
            v = run("validate", *BLOCKS, str(out))
            self.assertEqual(v.returncode, 0)
            self.assertIn("VALID", v.stdout)

    def test_json_output(self):
        r = run("solve", *BLOCKS, "-a", "ucs", "--json")
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "solved")
        self.assertEqual(data["cost"], 6)
        self.assertTrue(data["validation"]["valid"])

    def test_query(self):
        r = run("query", *BLOCKS, "On(x, y)")
        self.assertIn("x = C, y = A", r.stdout)

    def test_unify(self):
        r = run("unify", "Knows(John, x)", "Knows(y, Mother(y))")
        self.assertIn("{x/Mother(John), y/John}", r.stdout)

    def test_errors_exit_with_2(self):
        r = run("solve", str(ROOT / "domains/blocks/domain.fol"),
                str(ROOT / "domains/scheduling/project.fol"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("error:", r.stderr)


class TestMCPServer(unittest.TestCase):
    def call(self, messages):
        lines = "\n".join(json.dumps(m) for m in messages) + "\n"
        r = subprocess.run([sys.executable, "-m", "planner.mcp_server"], cwd=ROOT, input=lines,
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
        return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]

    def test_handshake_and_tools(self):
        out = self.call([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "planner_solve",
                        "arguments": {"domain": "blocks", "problem": "sussman",
                                      "algorithm": "astar", "heuristic": "hmax"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "planner_solve", "arguments": {"domain": "x", "problem": "y"}}},
        ])
        self.assertEqual([m["id"] for m in out], [1, 2, 3, 4])   # no reply to the notification
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "fol_planner_mcp")
        names = {t["name"] for t in out[1]["result"]["tools"]}
        self.assertIn("planner_solve", names)
        for t in out[1]["result"]["tools"]:
            self.assertTrue(t["annotations"]["readOnlyHint"])
        solved = out[2]["result"]
        self.assertFalse(solved["isError"])
        self.assertEqual(solved["structuredContent"]["cost"], 6)
        self.assertTrue(out[3]["result"]["isError"])

    def test_inline_sources(self):
        domain = (ROOT / "domains/blocks/domain.fol").read_text(encoding="utf-8")
        problem = (ROOT / "domains/blocks/reverse4.fol").read_text(encoding="utf-8")
        out = self.call([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "planner_solve",
                                     "arguments": {"domain": domain, "problem": problem}}}])
        self.assertEqual(out[0]["result"]["structuredContent"]["steps"], 8)


if __name__ == "__main__":
    unittest.main()
