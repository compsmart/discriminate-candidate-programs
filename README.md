# discriminate-candidate-programs

Detects when your examples fit more than one minimal program, then asks the
fewest questions needed to pick the right one.

A [Claude Code](https://claude.com/claude-code) skill. Dependency-free Python,
3.10 or newer.

## The problem

A program that passes every example is not thereby the right program.

Enumerative program synthesis searches by increasing size and returns the first
program that fits all the examples. When several programs of that same size fit
equally well, which one you get is decided by enumeration order, and nothing in
the output tells you a choice was made.

A held-out qualification set does not fix this. Qualification catches a program
that generalises *badly*. It cannot catch a program that generalises
*differently* in a direction your cases never looked, because a rival that
agrees on every case you happened to write also agrees on every case you
happened to hold out. When one person writes both files, they tend to share the
same blind spots.

Here is the situation, taken from
[example 1](examples/01-strict-vs-non-strict-boundary/). Count the strictly
positive values, with no zero anywhere in the examples:

```text
length(select(values, each_greater(values, 0)))         <- promoted
length(select(values, each_greater_equal(values, 0)))   <- fits just as well
```

The synthesis engine reports `PROMOTED` with `qualification_accuracy: 1.0`. Both
programs pass construction *and* qualification. They differ only on inputs
containing a zero. The winner was picked by the alphabetical ordering of
primitive keys.

## What this does

```text
UNDERDETERMINED: 2 survivors
  length(select(values, each_greater(values, 0)))
  length(select(values, each_greater_equal(values, 0)))

probe p001:  values = [0]
  survivor A predicts 0
  survivor B predicts 1

  -> what is the correct answer for [0]?

answer 0  ->  UNIQUE, strict program survives
answer 1  ->  UNIQUE, non-strict program survives
```

One question, aimed precisely at the boundary the examples never straddled. The
answer decides the program, and the probe is appended to the spec as an ordinary
case so the ambiguity cannot return.

## How it works

Three phases: enumerate, probe, resolve.

### 1. Enumerate the survivors

The stock search has two behaviours that, together, hide competing programs.

The obvious one is the **early halt**: the search returns the instant a
candidate matches every case, so same-size programs later in enumeration order
are never generated.

The subtle one matters more. The search collapses **observationally equivalent**
subexpressions — if a partial expression produces the same outputs on the
example cases as one already seen, it is not kept for reuse. This is sound and
valuable pruning for finding *a* program. But it means a rival subexpression is
never available for composition, so the rival program containing it cannot be
built at all.

So removing the early halt is *not sufficient*. In example 1 the tier-complete
search still finds only one winner (`winners_enumerated: 1`); the rival appears
only because the collapse now *records* what it discards. Each equivalence class
is keyed by output vector, and the winner is expanded by substituting class
members back in at every position.

Same-size members only. That keeps the recursion well-founded, since arguments
are always strictly smaller than the expression containing them, and it keeps
the claim honest: the question is which *equally minimal* programs fit, not
which larger ones also happen to.

### 2. Generate discriminating probes

Probe inputs come from a per-type corpus biased toward where ambiguity hides —
empty, singleton, zero, duplicates, negatives, permutations, case-varying
strings — plus seeded random values for width. Inputs already in the example
cases are excluded: every survivor agrees on those by definition, so they carry
no information.

Every survivor is evaluated on every sampled input. An input's value is how
finely it splits the survivor set, and selection is greedy — repeatedly take the
input that most refines the current partition. This keeps the number of
questions put to a human near the information-theoretic minimum. Two survivors
usually need exactly one probe.

Evaluation errors are recorded as a distinct outcome rather than dropped, so a
survivor that crashes on an input is separated from one that returns a value.

### 3. Resolve against an authority

A survivor is retained only if it predicts the authority's answer on every
answered probe. Three outcomes:

| Verdict | Meaning |
| --- | --- |
| `UNIQUE` | One survivor. The augmented spec now determines it. |
| `UNDERDETERMINED` | Several remain. Ask more probes, or supply cases. |
| `NO_SURVIVOR` | The authority contradicts every candidate. The target is outside this grammar — widen the vocabulary, do not re-run unchanged. |

The answered probes are appended to the spec and re-validated, so
`augmented-construction.json` is a drop-in replacement for the original. Both
phases write hash-chained ledgers.

**The authority must not be the search.** Answers come from a specification, a
reference implementation, a standard, or a person. Never from reasoning about
which candidate looks more natural — that is the search grading its own
homework. Crucially they must also not come from the qualification file, whose
hash is committed before search and which must stay unopened until a program is
frozen; drawing answers from it collapses the separation the design rests on.
Resolved probes become construction cases, never qualification cases.

## Install

Requires a synthesis engine installed alongside it, for the interpreter,
primitive library, and ledger. Sibling skill directories are searched in order:
`verified-logic-synthesizer` first, then `synthesize-verified-code`. Both
generations are supported and self-tested.

```bash
git clone https://github.com/compsmart/-discriminate-candidate-programs-skill.git \
  ~/.claude/skills/discriminate-candidate-programs
python ~/.claude/skills/discriminate-candidate-programs/scripts/discriminate.py self-test
```

```json
{"checks": "survivors, probes, resolution, augmentation, negative control, ledgers", "status": "PASS"}
```

If the engine lives elsewhere, pass `--engine /path/to/synthesize.py`.

The example specs declare the `svc-*-v3` schema, matching
`verified-logic-synthesizer`. Against the older engine, change those strings to
`v2`.

## Usage

```bash
D=~/.claude/skills/discriminate-candidate-programs/scripts/discriminate.py

python $D survivors --construction construction.json --output run
# stop here if determination is UNIQUE

python $D probes --survivors run/survivors.json --output probes.json
# answer the probes into answers.json

python $D resolve --survivors run/survivors.json --probes probes.json \
  --answers answers.json --output resolved

# then run the synthesis engine on resolved/augmented-construction.json
```

The answers file you write:

```json
{
  "schema": "dcp-answers-v1",
  "authority": "name the source that supplied these outputs",
  "answers": [{"probe_id": "p001", "expected": 0}]
}
```

`authority` is required and recorded in the resolution and the ledger. Label it
honestly — an authority the agent invented is local and exploratory, and the
result should be reported that way.

## Examples

- [1. Strict versus non-strict boundary](examples/01-strict-vs-non-strict-boundary/)
  — the ambiguity hides in the *values* the cases use. No case contains a zero,
  so nothing says whether zero counts.
- [2. Any versus all, hidden by singleton cases](examples/02-any-vs-all-singleton-cases/)
  — the ambiguity hides in the *shape* of the cases. On a one-element list,
  "any is negative" and "all are negative" are the same question. The generated
  probe is the empty list, which separates them and exercises the vacuous-truth
  boundary at the same time.

Both slip past a held-out qualification file. Each resolves with one question.

## Limits

Stated plainly, because the value of this tool is entirely in what it lets you
claim.

- **`UNIQUE` means no equally minimal rival exists.** A larger program may also
  fit. Minimality is the tie-break, not a proof. Say "unique among minimal
  programs in this grammar", never "the only program that fits".
- **Probe inputs are sampled, not exhaustive.** Failing to separate two
  survivors is evidence of agreement over the sample, not proof of equivalence.
  This is reported as `indistinguishable_groups` rather than hidden.
- **This is an interactive step.** The guarantee comes from an authority outside
  the search, so a batch run becomes one that stops and asks. That is the real
  cost.
- **It does not qualify, translate, or retain anything.** It decides which
  program the evidence picks out; the synthesis engine still does the rest.
- **`NO_SURVIVOR` is a finding, not a failure to retry.** The authority has
  excluded every program in the grammar.

## Relationship to the synthesis engine

This skill is a pre-freeze gate for a bounded typed-program synthesis engine —
`verified-logic-synthesizer` or its predecessor `synthesize-verified-code` —
which searches typed primitive compositions and qualifies a frozen winner
against held-out cases. It reuses the engine's interpreter, primitive library,
and hash-chained ledger, and modifies nothing in it: the engine's own self-test
and test suite still pass, and its `verify-ledger` validates the ledgers
written here.

Both engine generations share the early halt and the observational-equivalence
collapse, so both promote an arbitrary winner on an underdetermined spec. This
gate is independent of which one you run.

The design follows the R20 lesson from the PTSS cognition programme: when an
observation underdetermines the program, actively generate discriminating cases
rather than accepting the first survivor. The failure it prevents is the R8
one — an apparently successful program that fits the evaluation distribution
rather than the intent.

## Repository layout

```text
SKILL.md                  skill definition and workflow
scripts/discriminate.py   the engine: survivors, probes, resolve, self-test
references/method.md      method, file formats, worked example
examples/                 two runnable ambiguities with expected output
```
