# Exemple de planuri generate

Fișier generat automat de `scripts/generate_examples.py` (2026-11-19, Python 3.14.8). Toate planurile de mai jos au fost verificate de validatorul independent (`planner validate`): fiecare precondiție, axiomă și constrângere este adevărată la fiecare pas, iar scopul este atins.

Limite pentru tabelele comparative: 20 s și 100000 noduri expandate per rulare. `limit` / `timeout` înseamnă că algoritmul nu a terminat în aceste limite.

## Blocks World

### `blocks/sussman.fol` (SussmanAnomaly)

```text
The Sussman anomaly: achieving the two goals one after the other fails,
the planner has to interleave them.

    C
    A   B          A
  ---------   ->   B
                   C
```

Scop: `On(A, B) ∧ On(B, C)`

Plan de referință (A*, h = hmax):

```text
 1. Unstack(C, A)
 2. PutDown(C)
 3. PickUp(B)
 4. Stack(B, C)
 5. PickUp(A)
 6. Stack(A, B)
-- 6 pași, cost total 6; 10 noduri expandate
```

Comparație:

| Algorithm | Status | Steps | Cost | Expanded | Generated | Time (s) |
|---|---|---:|---:|---:|---:|---:|
| Breadth-first search | solved | 6 | 6 | 18 | 38 | 0.001 |
| Iterative deepening DFS | solved | 6 | 6 | 48 | 106 | 0.001 |
| Uniform-cost search | solved | 6 | 6 | 21 | 41 | 0.000 |
| Greedy best-first search, h = goal_count | solved | 6 | 6 | 13 | 28 | 0.000 |
| Greedy best-first search, h = hff | solved | 6 | 6 | 10 | 23 | 0.001 |
| A*, h = hmax | solved | 6 | 6 | 10 | 24 | 0.001 |
| A*, h = hadd | solved | 6 | 6 | 10 | 23 | 0.001 |
| A*, h = hff | solved | 6 | 6 | 10 | 23 | 0.001 |
| Weighted A*, h = hff, w = 2 | solved | 6 | 6 | 10 | 23 | 0.001 |

### `blocks/reverse4.fol` (ReverseTower4)

```text
Reverse a tower of four blocks.

  A            D
  B            C
  C     ->     B
  D            A
```

Scop: `On(D, C) ∧ On(C, B) ∧ On(B, A) ∧ OnTable(A)`

Plan de referință (A*, h = hmax):

```text
 1. Unstack(A, B)
 2. PutDown(A)
 3. Unstack(B, C)
 4. Stack(B, A)
 5. Unstack(C, D)
 6. Stack(C, B)
 7. PickUp(D)
 8. Stack(D, C)
-- 8 pași, cost total 8; 13 noduri expandate
```

Comparație:

| Algorithm | Status | Steps | Cost | Expanded | Generated | Time (s) |
|---|---|---:|---:|---:|---:|---:|
| Breadth-first search | solved | 8 | 8 | 27 | 76 | 0.003 |
| Iterative deepening DFS | solved | 8 | 8 | 69 | 175 | 0.002 |
| Uniform-cost search | solved | 8 | 8 | 47 | 123 | 0.001 |
| Greedy best-first search, h = goal_count | solved | 8 | 8 | 8 | 17 | 0.000 |
| Greedy best-first search, h = hff | solved | 8 | 8 | 8 | 17 | 0.001 |
| A*, h = hmax | solved | 8 | 8 | 13 | 34 | 0.001 |
| A*, h = hadd | solved | 8 | 8 | 8 | 17 | 0.001 |
| A*, h = hff | solved | 8 | 8 | 8 | 17 | 0.001 |
| Weighted A*, h = hff, w = 2 | solved | 8 | 8 | 8 | 17 | 0.001 |

### `blocks/derived_goal.fol` (AboveGoal)

```text
A goal stated with the derived predicate Above and a quantifier.
"E must end up somewhere above A, and B must be on the table with
 nothing on it" — the planner decides how tall the tower is.
```

Scop: `Above(E, A) ∧ OnTable(B) ∧ Clear(B)`

Plan de referință (A*, h = hmax):

```text
 1. Unstack(A, B)
 2. PutDown(A)
 3. Unstack(E, C)
 4. Stack(E, A)
-- 4 pași, cost total 4; 7 noduri expandate
```

Comparație:

| Algorithm | Status | Steps | Cost | Expanded | Generated | Time (s) |
|---|---|---:|---:|---:|---:|---:|
| Breadth-first search | solved | 4 | 4 | 9 | 23 | 0.001 |
| Iterative deepening DFS | solved | 4 | 4 | 16 | 43 | 0.001 |
| Uniform-cost search | solved | 4 | 4 | 16 | 46 | 0.001 |
| Greedy best-first search, h = goal_count | solved | 4 | 4 | 5 | 14 | 0.000 |
| Greedy best-first search, h = hff | solved | 4 | 4 | 4 | 12 | 0.001 |
| A*, h = hmax | solved | 4 | 4 | 7 | 20 | 0.001 |
| A*, h = hadd | solved | 4 | 4 | 7 | 20 | 0.001 |
| A*, h = hff | solved | 4 | 4 | 4 | 12 | 0.001 |
| Weighted A*, h = hff, w = 2 | solved | 4 | 4 | 4 | 12 | 0.001 |

### `blocks/one_tower.fol` (SingleTower)

```text
A universally quantified goal: build a single tower with D at the bottom.
The order of the other blocks is not specified; the planner picks one.
```

Scop: `OnTable(D) ∧ (∀x: Block(x) ∧ x ≠ D → Above(x, D))`

Plan de referință (A*, h = hmax):

```text
 1. Unstack(C, A)
 2. Stack(C, D)
 3. PickUp(A)
 4. Stack(A, C)
 5. PickUp(B)
 6. Stack(B, A)
-- 6 pași, cost total 6; 17 noduri expandate
```

Comparație:

| Algorithm | Status | Steps | Cost | Expanded | Generated | Time (s) |
|---|---|---:|---:|---:|---:|---:|
| Breadth-first search | solved | 6 | 6 | 45 | 119 | 0.004 |
| Iterative deepening DFS | solved | 6 | 6 | 95 | 262 | 0.003 |
| Uniform-cost search | solved | 6 | 6 | 67 | 178 | 0.002 |
| Greedy best-first search, h = goal_count | solved | 6 | 6 | 6 | 15 | 0.000 |
| Greedy best-first search, h = hff | solved | 6 | 6 | 6 | 15 | 0.001 |
| A*, h = hmax | solved | 6 | 6 | 17 | 52 | 0.003 |
| A*, h = hadd | solved | 6 | 6 | 6 | 17 | 0.001 |
| A*, h = hff | solved | 6 | 6 | 6 | 15 | 0.001 |
| Weighted A*, h = hff, w = 2 | solved | 6 | 6 | 6 | 15 | 0.001 |

### `blocks/large8.fol` (Large8)

```text
Eight blocks in three towers, rearranged into two different towers.
Too large for uninformed search within the default limits; used to
compare heuristics.

  H                       A    E
  C    G                  B    F
  A    E    D     ->      C    G
  B    F                  D    H
```

Scop: `On(A, B) ∧ On(B, C) ∧ On(C, D) ∧ OnTable(D) ∧ On(E, F) ∧ On(F, G) ∧ On(G, H) ∧ OnTable(H)`

Plan de referință (A*, h = hff):

```text
 1. Unstack(H, C)
 2. PutDown(H)
 3. Unstack(C, A)
 4. Stack(C, D)
 5. Unstack(G, E)
 6. Stack(G, H)
 7. Unstack(A, B)
 8. PutDown(A)
 9. PickUp(B)
10. Stack(B, C)
11. PickUp(A)
12. Stack(A, B)
13. Unstack(E, F)
14. PutDown(E)
15. PickUp(F)
16. Stack(F, G)
17. PickUp(E)
18. Stack(E, F)
-- 18 pași, cost total 18; 802 noduri expandate
```

Comparație:

| Algorithm | Status | Steps | Cost | Expanded | Generated | Time (s) |
|---|---|---:|---:|---:|---:|---:|
| Breadth-first search | timeout | - | - | 52736 | 221598 | 20.040 |
| Iterative deepening DFS | limit | - | - | 100001 | 430417 | 5.017 |
| Uniform-cost search | limit | - | - | 100001 | 400040 | 6.279 |
| Greedy best-first search, h = goal_count | solved | 18 | 18 | 44 | 175 | 0.005 |
| Greedy best-first search, h = hff | solved | 18 | 18 | 28 | 113 | 0.025 |
| A*, h = hmax | timeout | - | - | 34304 | 150795 | 20.082 |
| A*, h = hadd | solved | 18 | 18 | 95 | 411 | 0.077 |
| A*, h = hff | solved | 18 | 18 | 802 | 3324 | 0.667 |
| Weighted A*, h = hff, w = 2 | solved | 18 | 18 | 33 | 132 | 0.029 |

## Planificarea sarcinilor

### `scheduling/project.fol` (WebProject)

```text
A web application project with seven tasks and four people.

  Design --> Database --> Backend --> Testing --> Deploy
     \                      ^           ^          ^
      `-------------------> Frontend ---'---> Docs '
```

Scop: `∀t: Task(t) → Done(t)`

Plan de referință (A*, h = hmax):

```text
 1. Perform(Design, Ana)   (cost 9)
 2. Perform(Database, Bogdan)   (cost 4)
 3. Train(Dan, Python)   (cost 4)
 4. Perform(Backend, Dan)   (cost 5)
 5. Perform(Frontend, Carmen)   (cost 8)
 6. Perform(Docs, Carmen)   (cost 2)
 7. Perform(Testing, Dan)   (cost 2)
 8. Perform(Deploy, Ana)   (cost 3)
-- 8 pași, cost total 37; 210 noduri expandate
```

Comparație:

| Algorithm | Status | Steps | Cost | Expanded | Generated | Time (s) |
|---|---|---:|---:|---:|---:|---:|
| Breadth-first search | solved | 7 | 38 | 424 | 2254 | 0.205 |
| Iterative deepening DFS | solved | 7 | 38 | 9859 | 52016 | 4.188 |
| Uniform-cost search | solved | 8 | 37 | 760 | 3550 | 0.359 |
| Greedy best-first search, h = goal_count | solved | 8 | 42 | 8 | 52 | 0.007 |
| Greedy best-first search, h = hff | solved | 8 | 42 | 8 | 54 | 0.010 |
| A*, h = hmax | solved | 8 | 37 | 210 | 1171 | 0.103 |
| A*, h = hadd | solved | 7 | 38 | 7 | 48 | 0.007 |
| A*, h = hff | solved | 8 | 37 | 26 | 153 | 0.019 |
| Weighted A*, h = hff, w = 2 | solved | 7 | 38 | 7 | 48 | 0.008 |

### `scheduling/cyclic.fol` (CyclicDependencies)

```text
An inconsistent knowledge base: Backend and Testing depend on each other
through Integration. The rules derive Precedes(Backend, Backend), which
violates the axiom  forall t: ~Precedes(t, t), so the planner rejects the
problem before searching.
```

Scop: `∀t: Task(t) → Done(t)`

Plan de referință (A*, h = hmax):

> Baza de cunoștințe este inconsistentă: the initial state violates the axiom forall t: ~Precedes(t, t) (counterexample: t = Backend)

### `scheduling/missing_skill.fol` (MissingSkill)

```text
No plan exists: the audit needs the Security skill, nobody has it and
nobody can teach it. The relaxed reachability analysis behind the
heuristics proves this before a single node is expanded.
```

Scop: `∀t: Task(t) → Done(t)`

Plan de referință (A*, h = hmax):

> Nu există plan. Analiza de accesibilitate relaxată arată că scopul nu poate fi atins nici ignorând efectele negative (h(init) = inf), deci nu se expandează niciun nod.

## Robot de livrare

### `delivery/office.fol` (OfficeDelivery)

```text
Map:

  Office --- Hall --- Lab --- Vault (locked, opened by VaultKey)
              |
           Storage

The robot starts in the Hall. VaultKey lies in the Office.
  Parcel  : Storage -> Lab
  Sample  : Vault   -> Office
  Vase    : Lab     -> Storage   (fragile: carried alone)
```

Scop: `∀p: Package(p) → (∃r: Destination(p, r) ∧ At(p, r))`

Plan de referință (A*, h = hmax):

```text
 1. Move(Hall, Lab)
 2. Pick(Vase, Lab, Left)
 3. Move(Lab, Hall)
 4. Move(Hall, Storage)
 5. Drop(Vase, Storage, Left)
 6. Pick(Parcel, Storage, Left)
 7. Move(Storage, Hall)
 8. Move(Hall, Office)
 9. Pick(VaultKey, Office, Right)
10. Move(Office, Hall)
11. Move(Hall, Lab)
12. Drop(Parcel, Lab, Left)
13. Unlock(Vault, Lab, VaultKey)
14. Move(Lab, Vault)
15. Pick(Sample, Vault, Left)
16. Move(Vault, Lab)
17. Move(Lab, Hall)
18. Move(Hall, Office)
19. Drop(Sample, Office, Left)
-- 19 pași, cost total 19; 2276 noduri expandate
```

Comparație:

| Algorithm | Status | Steps | Cost | Expanded | Generated | Time (s) |
|---|---|---:|---:|---:|---:|---:|
| Breadth-first search | solved | 19 | 19 | 4991 | 16795 | 2.529 |
| Iterative deepening DFS | limit | - | - | 100001 | 354435 | 10.490 |
| Uniform-cost search | solved | 19 | 19 | 6279 | 20985 | 1.514 |
| Greedy best-first search, h = goal_count | solved | 21 | 21 | 117 | 391 | 0.013 |
| Greedy best-first search, h = hff | solved | 21 | 21 | 35 | 120 | 0.012 |
| A*, h = hmax | solved | 19 | 19 | 2276 | 7927 | 0.525 |
| A*, h = hadd | solved | 19 | 19 | 95 | 321 | 0.025 |
| A*, h = hff | solved | 19 | 19 | 390 | 1319 | 0.107 |
| Weighted A*, h = hff, w = 2 | solved | 19 | 19 | 82 | 268 | 0.023 |
