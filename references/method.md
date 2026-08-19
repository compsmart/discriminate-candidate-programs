# Method and file formats

## Contents

- Why the engine alone cannot see this
- Survivor enumeration
- Probe generation and selection
- Resolution
- File formats

## Why the engine alone cannot see this

`synthesize.py` has two behaviours that, together, hide competing programs.

The first is the early halt in `search_stage`: the moment a candidate matches
every construction case, the search returns. Programs of the same node count
later in enumeration order are never generated.

The second is subtler and matters more. The `admit` function collapses
observationally equivalent expressions — if a partial expression produces the
same outputs on the construction cases as one already seen, it is not added to
the bucket pool. This is a sound and valuable pruning rule for finding *a*
program. But it means a rival subexpression is never available for composition,
so the rival program containing it cannot be built at all. Finishing the tier
does not recover it. In the worked example below, tier completion alone yields
one winner; the rival appears only through equivalence-class expansion.

So the collapse that makes the search fast is also what makes the ambiguity
invisible. Recovering it requires recording what the collapse discarded.

## Survivor enumeration

`enumerate_survivors` mirrors the engine's search with two changes:

1. On a winner, it records the program and keeps enumerating to the end of the
   current node count, then stops. It never enumerates a larger size than the
   engine would.
2. `admit` records every collapsed member in an equivalence class keyed by
   `(result type, digest of the output vector)` rather than discarding it. Only
   the first member enters the bucket pool, so branching cost is unchanged.

`expand_winners` then substitutes, at every position in each winner, the class
members of the same node count. Same size only, for two reasons: it keeps the
recursion well-founded, since arguments are always strictly smaller than the
expression containing them, and it keeps the claim honest — the question is
which equally minimal programs fit, not which larger ones also happen to.

Every expanded variant is re-evaluated against the construction cases before it
is admitted as a survivor. Substituting an equivalent subexpression preserves
outputs by compositionality, so this should never reject anything; it is a
cheap guard against divergent exception behaviour.

## Probe generation and selection

Probe inputs come from a per-type corpus biased toward the conditions that
ambiguity hides behind: empty, singleton, zero, duplicates, negatives,
permutations, and case-varying strings, plus seeded random values for width.
Inputs already present in the construction cases are excluded, since they
cannot discriminate — every survivor agrees on them by definition.

Each survivor is evaluated on each sampled input. An input's value is how
finely it splits the survivor set. Selection is greedy: repeatedly take the
input that most refines the current partition, until every survivor is
separated or `--max-probes` is reached. This keeps the number of questions put
to a human close to the information-theoretic minimum — two survivors usually
need exactly one probe.

An evaluation error is recorded as a distinct outcome rather than discarded, so
a survivor that crashes on an input is separated from one that returns a value.
Such a survivor is eliminated at resolution, since it matches no expected value.

Selection is deterministic given `--seed`. Two runs with the same seed and
survivors produce the same probes.

## Resolution

A survivor is retained only if it predicts the authority's answer on every
answered probe. Three outcomes:

- `UNIQUE` — one survivor. The augmented construction spec now determines it.
- `UNDERDETERMINED` — several remain. Ask more probes or supply cases.
- `NO_SURVIVOR` — the authority contradicts every candidate. The target program
  is not in the grammar. This is a real finding: treat it as a signal to widen
  the vocabulary or node budget, not as a run to repeat unchanged.

Answered probes are appended to the construction spec as ordinary cases and the
result is re-validated by the engine's own `validate_spec`, so the augmented
file is a drop-in input to `synthesize.py`.

Both phases write hash-chained ledgers using the engine's `Ledger`, verifiable
with `synthesize.py verify-ledger`.

## Worked example

Counting strictly positive values, with cases `[-2,3,4] -> 2`, `[-3,-1] -> 0`,
and `[5] -> 1`, and a vocabulary containing both `each_greater` and
`each_greater_equal`. No case contains a zero.

Two programs of six nodes fit every case:

```text
length(select(values, each_greater(values, 0)))
length(select(values, each_greater_equal(values, 0)))
```

The stock engine promotes the first with qualification accuracy 1.0 and reports
no ambiguity, because the held-out cases contain no zero either. Both programs
pass both gates. The choice was made by the alphabetical ordering of primitive
keys in the enumeration loop.

This skill reports `UNDERDETERMINED`, asks one question — what is the answer
for `[0]`? — and the answer decides it. Answer `0` and the strict program
survives; answer `1` and the non-strict one does. The evidence picks the
program, rather than the enumeration order picking it.

## File formats

### survivors.json (`dcp-survivors-v1`)

Written by `survivors`. Carries the raw construction spec and its normalized
digest so later phases cannot be crossed with a different spec, the survivor
list with stable `s-` prefixed ids, search counters, and `determination`.

### probes.json (`dcp-probes-v1`)

Written by `probes`. Each probe has an id, the input assignment, and every
survivor's prediction. `separates_all_survivors` and `indistinguishable_groups`
report what the sample could not split.

### answers file (`dcp-answers-v1`)

Written by you.

```json
{
  "schema": "dcp-answers-v1",
  "authority": "name the source that supplied these outputs",
  "answers": [{"probe_id": "p001", "expected": 0}]
}
```

`authority` is required and non-empty; it is copied into the resolution and the
ledger. Expected values are type-checked against the spec's output type.

### resolution.json (`dcp-resolution-v1`) and augmented-construction.json

Written by `resolve`. The resolution carries the verdict, the authority, the
before and after survivor counts, the remaining programs, the resolved cases,
and guidance for the verdict. The augmented spec is the original with the
answered probes appended.

## Self-test

```bash
python scripts/discriminate.py self-test
```

Runs the worked example end to end and asserts: the observation is detected as
underdetermined; both boundary programs are recovered; the generated probe
exercises the zero boundary; resolution yields the strict program uniquely; the
augmented spec is no longer ambiguous; an authority inconsistent with every
candidate yields `NO_SURVIVOR`; and both ledgers verify.
