# Limbajul de descriere a domeniilor (`.fol`)

Un domeniu și o problemă se scriu în fișiere text separate, ca în PDDL, dar
condițiile și scopurile sunt formule de logică de ordinul întâi (FOL) în
notația din Russell & Norvig.

## Convenții lexicale

| Element | Regulă | Exemple |
|---|---|---|
| Variabilă | începe cu literă mică | `x`, `t1`, `from` |
| Constantă / obiect | începe cu literă mare sau cifră | `A`, `Ana`, `Hall`, `3` |
| Predicat | nume urmat de argumente; fără argumente = propoziție | `On(x, y)`, `HandEmpty` |
| Funcție (termen) | nume urmat de argumente, în poziție de argument | `Mother(John)` |
| Comentariu | de la `#` până la sfârșitul liniei | `# observație` |

Un câmp (`pre:`, `goal:` etc.) poate continua pe liniile următoare: orice linie
care nu începe cu un cuvânt-cheie urmat de `:` se lipește de câmpul curent.

## Formule

De la precedența cea mai mică la cea mai mare:

| Operator | ASCII | Unicode | Observații |
|---|---|---|---|
| echivalență | `<->` | `↔` | |
| implicație | `->` | `→` | asociativă la dreapta |
| disjuncție | `\|` sau `or` | `∨` | |
| conjuncție | `&`, `,` sau `and` | `∧` | virgula e conjuncție |
| negație | `~`, `!` sau `not` | `¬` | |
| cuantificatori | `forall x, y: F` | `∀x, y: F` | corpul se extinde cât mai la dreapta; separatorul poate fi `:` sau `.` |
| | `exists x: F` | `∃x: F` | |
| egalitate | `x = y`, `x != y` | `x ≠ y` | |
| constante | `true`, `false` | `⊤`, `⊥` | |

Exemple:

```text
forall x, y: On(x, y) -> ~Clear(y)
exists g: Holds(g, k)
forall t: Task(t) -> Done(t)
~(exists w, t1, t2, t3: DoneBy(t1, w) & DoneBy(t2, w) & DoneBy(t3, w)
                        & t1 != t2 & t1 != t3 & t2 != t3)
```

### Semantică

Fiecare stare este un **model Herbrand finit** sub **ipoteza lumii închise**:
un atom este adevărat dacă și numai dacă apare în stare (sau se deduce din
reguli). Cuantificatorii iau valori în mulțimea obiectelor declarate
(`objects:` din problemă plus `constants:` din domeniu).

## Fișierul de domeniu

```text
domain <Nume>

types:       T1, T2, ...                  # tipurile sunt predicate unare
constants:   C1, C2 : T1; C3              # obiecte comune tuturor problemelor (opțional)
predicates:  P(x, y), Q(x), R, ...        # toate predicatele, cu aritatea lor

derive:      <corp> -> <Cap>              # regulă Horn (oricâte)
axiom:       <formulă închisă>            # invariant al domeniului (oricâte)
constraint:  <formulă închisă>            # restricție impusă în căutare (oricâte)

action <Nume>(x: T1, y: T2, z)            # parametrii pot avea tip
    pre:  <formulă FOL>                   # implicit: true
    eff:  <literal> & <literal> & ...     # pozitiv = adăugat, negat = șters
    cost: <expresie numerică>             # implicit: 1
```

### Reguli (`derive:`)

Clauze definite, cuantificate universal implicit:

```text
derive: On(x, y) -> Above(x, y)
derive: On(x, y) & Above(y, z) -> Above(x, z)
```

- capul este un singur atom; corpul o conjuncție de literali;
- fiecare variabilă din cap și din literalii negați trebuie să apară într-un
  atom pozitiv din corp (reguli *sigure*);
- negația este permisă doar pe predicate care nu sunt derivate (negație
  stratificată);
- predicatele derivate nu pot apărea în efectele acțiunilor și nici în starea
  inițială: valoarea lor rezultă exclusiv din reguli.

Regulile ale căror corpuri depind doar de predicate statice se evaluează o
singură dată; celelalte se recalculează în fiecare stare prin forward chaining
semi-naiv.

### Axiome și constrângeri

- `axiom:` descrie un **invariant**: ceva ce domeniul garantează în orice stare
  accesibilă (de exemplu „niciun bloc nu e deasupra lui însuși”). Axiomele se
  verifică pe starea inițială (o încălcare înseamnă bază de cunoștințe
  inconsistentă, iar planificatorul refuză problema și indică un
  contraexemplu) și pe fiecare stare a planului, de către validator. Cu
  `--check-axioms` se verifică și în timpul căutării.
- `constraint:` descrie o **restricție de siguranță**: stările care o încalcă
  sunt eliminate din căutare (de exemplu „obiectul fragil se transportă
  singur”).

Variabilele libere dintr-o axiomă sunt citite universal.

### Acțiuni

- Parametrii cu tip (`x: Block`) adaugă implicit precondiția `Block(x)`. Tipul
  poate fi un tip declarat sau orice predicat unar static (de exemplu `Item`,
  derivat din `Package` și `Key`).
- Precondiția poate fi orice formulă FOL ale cărei variabile libere sunt
  parametri; poate conține cuantificatori, disjuncții, negații și egalități.
- Efectul este o conjuncție de literali (semantica STRIPS: întâi se șterg
  literalii negați, apoi se adaugă cei pozitivi).
- Costul este o expresie cu `+ - * /`, numere și funcții numerice ale căror
  valori se dau în problemă, de exemplu `cost: Size(t) * Rate(w)`.

## Fișierul de problemă

```text
problem <Nume>
domain: <NumeDomeniu>                     # verificat la încărcare

objects: A, B, C : Block; Ana, Dan : Worker; Extra
init:    On(C, A), OnTable(A), Clear(C), HandEmpty      # atomi ground
values:  Size(Design) = 3, Rate(Ana) = 3                # valori pentru costuri
goal:    <formulă FOL>
```

- `init` conține doar atomi ground pozitivi; tot ce lipsește este fals.
- Scopul poate fi orice formulă. Variabilele libere sunt citite **existențial**:
  `goal: On(x, C) & OnTable(x)` înseamnă „un bloc oarecare pe C, care stă pe masă”.

## Fișierul de plan

Câte o acțiune pe linie; numerotarea și comentariile (`#`, `;`) sunt opționale:

```text
# plan pentru sussman.fol
1. Unstack(C, A)
2. PutDown(C)
PickUp(B)
```

## Mesaje de eroare

Parserul și verificările statice raportează fișierul și linia:

```text
error: domains/x/domain.fol:31: unexpected ')' in: Holding(x))
error: action Stack (line 40): precondition uses variables that are not parameters: z
error: action Move (line 12): derived predicate(s) Connected cannot appear in effects; they follow from the rules
error: init: Above(A, B) is a derived predicate; it is computed from the rules and must not be listed
```
