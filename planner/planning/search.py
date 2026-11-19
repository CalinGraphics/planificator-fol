"""State-space search algorithms (progression / forward search).

    bfs      breadth-first, graph search          complete, optimal in #steps
    dfs      depth-first, graph search            complete (finite), not optimal
    iddfs    iterative deepening DFS              complete, optimal in #steps
    ucs      uniform-cost (Dijkstra)              complete, optimal in cost
    greedy   greedy best-first, f = h             complete (finite), not optimal
    astar    A*, f = g + h                        optimal if h is admissible
    wastar   weighted A*, f = g + w*h             cost <= w * optimal (admissible h)
"""

from __future__ import annotations

import heapq
import time
from collections import deque
from dataclasses import dataclass, field
from itertools import count
from math import inf

from .heuristics import Heuristic, make_heuristic
from .task import PlanningTask

ALGORITHMS = ("bfs", "dfs", "iddfs", "ucs", "greedy", "astar", "wastar")
INFORMED = {"greedy", "astar", "wastar"}

ALGORITHM_NAMES = {
    "bfs": "Breadth-first search",
    "dfs": "Depth-first search",
    "iddfs": "Iterative deepening DFS",
    "ucs": "Uniform-cost search",
    "greedy": "Greedy best-first search",
    "astar": "A*",
    "wastar": "Weighted A*",
}


@dataclass
class SearchStats:
    expanded: int = 0
    generated: int = 0
    pruned_by_constraints: int = 0
    max_frontier: int = 0
    elapsed: float = 0.0


@dataclass
class SearchResult:
    status: str                      # solved | unsolvable | limit | timeout | inconsistent
    plan: list = field(default_factory=list)
    cost: float = inf
    stats: SearchStats = field(default_factory=SearchStats)
    algorithm: str = ""
    heuristic: str | None = None
    weight: float | None = None
    initial_h: float | None = None
    note: str = ""

    @property
    def solved(self) -> bool:
        return self.status == "solved"


class _Node:
    __slots__ = ("state", "parent", "action", "g", "depth")

    def __init__(self, state, parent, action, g, depth):
        self.state = state
        self.parent = parent
        self.action = action
        self.g = g
        self.depth = depth

    def path(self) -> list:
        actions = []
        node = self
        while node.parent is not None:
            actions.append(node.action)
            node = node.parent
        actions.reverse()
        return actions


class _Limit(Exception):
    def __init__(self, status):
        self.status = status


class Searcher:
    def __init__(self, task: PlanningTask, mode: str = "grounded",
                 max_expansions: int = 500_000, timeout: float = 120.0):
        self.task = task
        self.mode = mode
        self.max_expansions = max_expansions
        self.timeout = timeout
        self.stats = SearchStats()
        self._start = 0.0

    # -- shared helpers ------------------------------------------------------

    def _successors(self, state):
        task = self.task
        check = task.check_in_search
        kinds = task.search_kinds
        for action in task.applicable(state, self.mode):
            child = task.apply(state, action)
            self.stats.generated += 1
            if check and task.violated_axioms(child, kinds):
                self.stats.pruned_by_constraints += 1
                continue
            yield action, child

    def _tick(self):
        self.stats.expanded += 1
        if self.stats.expanded > self.max_expansions:
            raise _Limit("limit")
        if self.stats.expanded % 512 == 0 and time.perf_counter() - self._start > self.timeout:
            raise _Limit("timeout")

    def run(self, algorithm: str, heuristic: str | Heuristic | None = None,
            weight: float = 2.0, depth_limit: int | None = None) -> SearchResult:
        if algorithm not in ALGORITHMS:
            raise ValueError(f"unknown algorithm {algorithm!r}; choose from {', '.join(ALGORITHMS)}")
        h = None
        if algorithm in INFORMED:
            h = heuristic if isinstance(heuristic, Heuristic) else \
                make_heuristic(heuristic or "hff", self.task)
        self.stats = SearchStats()
        self._start = time.perf_counter()
        result = SearchResult(status="unsolvable", algorithm=algorithm,
                              heuristic=h.name if h else None,
                              weight=weight if algorithm == "wastar" else None)
        violated = self.task.violated_axioms(self.task.init) if self.task.axioms else []
        if violated:
            from ..logic.syntax import to_text
            ax, cex, kind = violated[0]
            witness = ", ".join(f"{v} = {c}" for v, c in cex.items())
            result.status = "inconsistent"
            result.note = (f"the initial state violates the {kind} {to_text(ax)}"
                           + (f" (counterexample: {witness})" if witness else ""))
            self.stats.elapsed = time.perf_counter() - self._start
            result.stats = self.stats
            return result
        try:
            if algorithm == "bfs":
                node = self._bfs()
            elif algorithm == "dfs":
                node = self._dfs(depth_limit)
            elif algorithm == "iddfs":
                node = self._iddfs(depth_limit)
            else:
                if algorithm == "ucs":
                    f = lambda g, hv: g
                    hfun = lambda s: 0.0
                elif algorithm == "greedy":
                    f = lambda g, hv: hv
                    hfun = h
                elif algorithm == "astar":
                    f = lambda g, hv: g + hv
                    hfun = h
                else:
                    f = lambda g, hv: g + weight * hv
                    hfun = h
                if h is not None:
                    result.initial_h = h(self.task.init)
                node = self._best_first(f, hfun, reopen=algorithm != "greedy")
            if node is not None:
                result.status = "solved"
                result.plan = node.path()
                result.cost = node.g
        except _Limit as e:
            result.status = e.status
        self.stats.elapsed = time.perf_counter() - self._start
        result.stats = self.stats
        if result.status == "unsolvable" and algorithm == "dfs" and depth_limit is not None:
            result.note = f"no plan within depth {depth_limit}"
        return result

    # -- uninformed ----------------------------------------------------------

    def _bfs(self):
        task = self.task
        root = _Node(task.init, None, None, 0.0, 0)
        if task.is_goal(root.state):
            return root
        frontier = deque([root])
        visited = {root.state}
        while frontier:
            node = frontier.popleft()
            self._tick()
            for action, child in self._successors(node.state):
                if child in visited:
                    continue
                visited.add(child)
                n = _Node(child, node, action, node.g + action.cost, node.depth + 1)
                if task.is_goal(child):
                    return n
                frontier.append(n)
            self.stats.max_frontier = max(self.stats.max_frontier, len(frontier))
        return None

    def _dfs(self, depth_limit):
        task = self.task
        root = _Node(task.init, None, None, 0.0, 0)
        stack = [root]
        visited = {root.state}
        while stack:
            node = stack.pop()
            if task.is_goal(node.state):
                return node
            if depth_limit is not None and node.depth >= depth_limit:
                continue
            self._tick()
            children = []
            for action, child in self._successors(node.state):
                if child not in visited:
                    visited.add(child)
                    children.append(_Node(child, node, action, node.g + action.cost,
                                          node.depth + 1))
            stack.extend(reversed(children))   # explore in natural order
            self.stats.max_frontier = max(self.stats.max_frontier, len(stack))
        return None

    def _iddfs(self, depth_limit):
        task = self.task
        max_depth = depth_limit if depth_limit is not None else 10_000
        root = _Node(task.init, None, None, 0.0, 0)
        for limit in range(max_depth + 1):
            cutoff = False
            # stack of (node, iterator over successors); cycle check along the path
            on_path = {root.state}
            if task.is_goal(root.state):
                return root
            stack = [(root, None)]
            while stack:
                node, it = stack[-1]
                if it is None:
                    if node.depth >= limit:
                        cutoff = True
                        stack.pop()
                        on_path.discard(node.state)
                        continue
                    self._tick()
                    it = iter(list(self._successors(node.state)))
                    stack[-1] = (node, it)
                advanced = False
                for action, child in it:
                    if child in on_path:
                        continue
                    n = _Node(child, node, action, node.g + action.cost, node.depth + 1)
                    if task.is_goal(child):
                        return n
                    on_path.add(child)
                    stack.append((n, None))
                    advanced = True
                    break
                if not advanced:
                    stack.pop()
                    on_path.discard(node.state)
                self.stats.max_frontier = max(self.stats.max_frontier, len(stack))
            if not cutoff:
                return None
        return None

    # -- best-first family (UCS, greedy, A*, weighted A*) --------------------

    def _best_first(self, f, h, reopen: bool):
        task = self.task
        tie = count()
        h_cache = {}

        def hval(state):
            v = h_cache.get(state)
            if v is None:
                v = h(state)
                h_cache[state] = v
            return v

        root = _Node(task.init, None, None, 0.0, 0)
        h0 = hval(root.state)
        if h0 == inf:
            return None
        frontier = [(f(0.0, h0), h0, next(tie), root)]
        best_g = {root.state: 0.0}
        closed = set()
        while frontier:
            _, _, _, node = heapq.heappop(frontier)
            state = node.state
            if node.g > best_g.get(state, inf):
                continue                       # stale entry
            if not reopen:
                if state in closed:
                    continue
                closed.add(state)
            if task.is_goal(state):
                return node
            self._tick()
            for action, child in self._successors(state):
                g = node.g + action.cost
                if reopen:
                    if g >= best_g.get(child, inf):
                        continue
                elif child in closed or child in best_g:
                    continue
                hv = hval(child)
                if hv == inf:
                    continue                   # dead end: goal unreachable even when relaxed
                best_g[child] = g
                heapq.heappush(frontier, (f(g, hv), hv, next(tie),
                                          _Node(child, node, action, g, node.depth + 1)))
            if len(frontier) > self.stats.max_frontier:
                self.stats.max_frontier = len(frontier)
        return None


def plan(task: PlanningTask, algorithm: str = "astar", heuristic: str = "hff",
         mode: str = "grounded", weight: float = 2.0, depth_limit=None,
         max_expansions: int = 500_000, timeout: float = 120.0) -> SearchResult:
    """Convenience wrapper: run one search and return its result."""
    searcher = Searcher(task, mode, max_expansions, timeout)
    return searcher.run(algorithm, heuristic, weight, depth_limit)
