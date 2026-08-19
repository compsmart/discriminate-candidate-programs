# Example 1: strict versus non-strict boundary

Count the strictly positive values in a list. The examples never contain a
zero, so they cannot say whether zero counts.

## The ambiguity

Two programs of six nodes fit every construction case:

```text
length(select(values, each_greater(values, 0)))
length(select(values, each_greater_equal(values, 0)))
```

They differ only on inputs containing a zero. No construction case has one, and
neither does the qualification file, so both programs pass both gates.

## What the synthesis engine alone does

```bash
python .../synthesize.py search \
  --construction construction.json \
  --qualification qualification.json \
  --output stock
```

```text
status: PROMOTED
qualification_accuracy: 1.0
expression: length(select(values, each_greater(values, 0)))
```

It promotes the strict program with a perfect score and reports no ambiguity.
That looks like a clean result, but the rival passes every case in both files
too. The winner was decided by the alphabetical ordering of primitive keys in
the enumeration loop, not by anything in the evidence.

## What this skill does

```bash
python .../discriminate.py survivors --construction construction.json --output run
```

```text
determination: UNDERDETERMINED
survivor_count: 2
  s-8f0e87ce0022 | length(select(values, each_greater(values, 0)))
  s-be458ab57b82 | length(select(values, each_greater_equal(values, 0)))
```

Note `winners_enumerated: 1` in the search block against `survivor_count: 2`.
Simply letting the search run to the end of the tier would not have found the
rival: its subexpression `each_greater_equal(values, 0)` was collapsed as
observationally equivalent, so the rival could never be composed. It is
recovered by equivalence-class expansion.

```bash
python .../discriminate.py probes --survivors run/survivors.json --output probes.json
```

```text
probe_count: 1
p001  values=[0]   s-8f0e... predicts 0   s-be45... predicts 1
```

One question, aimed exactly at the boundary the cases never straddled.

```bash
python .../discriminate.py resolve \
  --survivors run/survivors.json --probes probes.json \
  --answers answers.json --output resolved
```

```text
verdict: UNIQUE   2 -> 1
winner: length(select(values, each_greater(values, 0)))
```

## The point

The supplied `answers.json` says zero does not count, and the strict program
wins. Change `expected` to `1` and the non-strict program wins instead. The
answer decides the program.

That is the whole difference. The engine alone also returns the strict program
here, but it would have returned it either way. After resolution the choice is
attributable to a stated authority, and `resolved/augmented-construction.json`
carries the probe forward as an ordinary case so the ambiguity cannot come back.
