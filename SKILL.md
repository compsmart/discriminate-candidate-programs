---
name: discriminate-candidate-programs
description: Detect when synthesised example cases underdetermine the answer, enumerate every equally minimal program that fits, generate the inputs on which those programs disagree, and resolve them against an external authority before a winner is frozen. Use before promoting or translating a synthesised program, when a passing program might be fitting the examples rather than the intent, when boundary or multiplicity behaviour was never exercised by the visible cases, or when an implementation must be shown unique rather than merely first. Do not use to invent expected outputs, to judge subjective behaviour, or as a substitute for a held-out qualification gate.
---

# Discriminate Candidate Programs

A program that passes every visible example is not thereby the right program.
When several equally minimal programs fit the same examples, the one you get is
decided by enumeration order. This skill turns that silent choice into an
explicit question.

Require Python 3.10 or newer. Dependency-free. It reuses the interpreter,
primitive library, and ledger from `synthesize-verified-code`; that skill must
be installed alongside it, or pass `--engine` with the path to `synthesize.py`.

On Windows, run each command on one line. The line continuations below work in
Git Bash and PowerShell respectively.

## When this matters

The synthesis engine halts at the first minimal program that fits all
construction cases, then checks it against held-out qualification cases. Both
gates can pass while the program is wrong, because a rival program that agrees
on every case you happened to write also agrees on every case you happened to
hold out. Qualification catches a program that generalises badly. It cannot
catch a program that generalises differently in a direction your cases never
looked.

Reach for this skill when the answer depends on a boundary the examples do not
straddle: strict versus non-strict comparison, empty and singleton inputs,
duplicates, tie-breaking, ordering, any versus all.

## Workflow

1. Write the construction spec exactly as `synthesize-verified-code` requires.
   Use the same file for both skills.

2. Enumerate every equally minimal program that fits:

   ```bash
   python C:/Users/brad/.claude/skills/discriminate-candidate-programs/scripts/discriminate.py survivors \
     --construction construction.json \
     --output .dcp/run-001
   ```

   Read `determination` in the result:

   - `UNIQUE` — one program of minimal size fits. Proceed to the synthesis
     engine as normal; nothing here is needed.
   - `UNDERDETERMINED` — the cases do not pick out a single program. Continue.
   - `NO_SURVIVOR` — nothing in this grammar and node budget fits. Widen the
     vocabulary or `max_nodes`; do not re-run unchanged.

3. Generate the inputs on which the survivors disagree:

   ```bash
   python .../discriminate.py probes \
     --survivors .dcp/run-001/survivors.json \
     --output .dcp/run-001/probes.json
   ```

   Each probe shows what every survivor predicts. Probes are chosen greedily to
   split the survivor set fastest, so the count is usually far smaller than the
   number of survivors. `separates_all_survivors: false` means some survivors
   agree on every sampled input; raise `--sample`, or accept that they are
   indistinguishable over the sampled domain and say so.

4. Answer the probes from an authority that is not the search. A specification,
   a reference implementation, a standard, or the user. Write:

   ```json
   {
     "schema": "dcp-answers-v1",
     "authority": "user-held-specification-strictly-positive",
     "answers": [{"probe_id": "p001", "expected": 0}]
   }
   ```

   Never answer a probe by reasoning about which candidate looks more natural.
   That is the search grading its own homework. If no authority can answer,
   report the ambiguity and stop.

5. Resolve:

   ```bash
   python .../discriminate.py resolve \
     --survivors .dcp/run-001/survivors.json \
     --probes .dcp/run-001/probes.json \
     --answers .dcp/run-001/answers.json \
     --output .dcp/run-001/resolved
   ```

   This writes `resolution.json` and `augmented-construction.json` — the
   original spec with the answered probes appended as ordinary cases.

6. Run `synthesize-verified-code` on `augmented-construction.json` against your
   untouched qualification file, then qualify, translate, and retain as usual.
   The augmented spec is now strong enough that the engine's first winner is
   also its only winner.

Verify either ledger with the engine's own verifier:

```bash
python .../synthesize.py verify-ledger --ledger .dcp/run-001/probe-ledger.jsonl
```

## Boundaries

- `UNIQUE` means no *equally minimal* rival exists. A larger program may also
  fit; minimality is the tie-break, not a proof. Say "unique among minimal
  programs in this grammar", never "the only program that fits".
- Probe answers must not come from the qualification file. That file's hash is
  committed before search and must stay unopened until a program is frozen;
  drawing answers from it collapses the separation the whole design rests on.
  Resolved probes become construction cases, never qualification cases.
- Probe inputs are sampled, not exhaustive. Failing to separate two survivors
  is evidence of agreement over the sample, not proof of equivalence.
- `NO_SURVIVOR` after resolution is informative, not a failure to retry: the
  authority has excluded every program in the grammar, so the target is outside
  it. Widen the vocabulary rather than re-running.
- The authority is recorded verbatim in the resolution and the ledger. Label it
  honestly. An authority the agent invented is local and exploratory, and the
  result should be reported that way.
- This skill does not qualify, translate, or retain anything. It only decides
  which program the evidence actually picks out.

## Handoff

Report the survivor count and determination, the probes asked and who answered
them, the surviving program, what the eliminated rivals would have done
differently, the path to the augmented construction spec, and whether the
authority was independent or local.
