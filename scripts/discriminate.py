#!/usr/bin/env python3
"""Discriminating-probe resolution for underdetermined program synthesis.

The bundled synthesis engine halts at the first minimal program that fits every
construction case. When several distinct programs of that same size fit equally
well, that choice is made by enumeration order, not by evidence. This tool
enumerates the full survivor set, generates inputs on which survivors disagree,
and resolves them against an external authority before anything is frozen.

It never invents expected outputs. It only asks questions.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import itertools
import json
import random
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


SURVIVORS_SCHEMA = "dcp-survivors-v1"
PROBES_SCHEMA = "dcp-probes-v1"
ANSWERS_SCHEMA = "dcp-answers-v1"
RESOLUTION_SCHEMA = "dcp-resolution-v1"

DEFAULT_ENGINE = (
    Path(__file__).resolve().parent.parent.parent
    / "synthesize-verified-code" / "scripts" / "synthesize.py"
)


class DiscriminationError(RuntimeError):
    pass


def load_engine(explicit: Path | None) -> Any:
    path = (explicit or DEFAULT_ENGINE).resolve()
    if not path.is_file():
        raise DiscriminationError(
            f"synthesis engine not found at {path}; pass --engine with the path to synthesize.py"
        )
    spec = importlib.util.spec_from_file_location("svc_engine", path)
    if spec is None or spec.loader is None:
        raise DiscriminationError(f"cannot load synthesis engine from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["svc_engine"] = module
    spec.loader.exec_module(module)
    for required in ("Expr", "primitive_library", "validate_spec", "evaluate", "digest", "Ledger"):
        if not hasattr(module, required):
            raise DiscriminationError(f"engine at {path} is missing {required}; version mismatch")
    return module


# The v2 engine (synthesize-verified-code) and the v3 engine
# (verified-logic-synthesizer) differ in three signatures: v3 added resource
# limits to evaluation and a byte cap to the ledger. These shims let one script
# drive either engine.
#
# The arity is inspected once rather than discovered by catching TypeError at
# the call site. A genuine TypeError raised from inside evaluation is an
# ordinary candidate failure, and must not be mistaken for a signature
# mismatch and silently retried.

def accepts_limits(function: Any, count: int) -> bool:
    import inspect
    try:
        return len(inspect.signature(function).parameters) >= count
    except (TypeError, ValueError):
        return False


def make_ledger(E: Any, path: Path, limits: Mapping[str, Any]) -> Any:
    if accepts_limits(E.Ledger.__init__, 3):
        return E.Ledger(path, int(limits.get("max_ledger_bytes", 67_108_864)))
    return E.Ledger(path)


def outcomes(E: Any, expr: Any, cases: Sequence[Mapping[str, Any]],
             primitives: Mapping[str, Any], limits: Mapping[str, Any]) -> tuple[Any, ...] | None:
    if accepts_limits(E.outcome_vector, 4):
        return E.outcome_vector(expr, cases, primitives, limits)
    return E.outcome_vector(expr, cases, primitives)


def evaluate_one(E: Any, expr: Any, inputs: Mapping[str, Any],
                 primitives: Mapping[str, Any], limits: Mapping[str, Any]) -> Any:
    if accepts_limits(E.evaluate, 4):
        return E.evaluate(expr, inputs, primitives, limits)
    return E.evaluate(expr, inputs, primitives)


# --------------------------------------------------------------------------
# Survivor enumeration
# --------------------------------------------------------------------------

def enumerate_survivors(E: Any, spec: Mapping[str, Any], primitives: Mapping[str, Any],
                        limits: Mapping[str, int], max_variants: int) -> dict[str, Any]:
    """Enumerate every minimal program that fits all construction cases.

    Differs from the engine's search_stage in two ways:
      1. it does not halt on the first winner; it finishes the node-count tier;
      2. it records observational-equivalence classes instead of discarding the
         collapsed members, then expands winners through those classes.
    Both are required: the collapse itself hides competing programs, because a
    subexpression that agrees on every construction case is never re-used.
    """
    cases = spec["cases"]
    expected = tuple(case["expected"] for case in cases)
    buckets: dict[tuple[str, int], list[Any]] = {}
    classes: dict[tuple[str, str], list[Any]] = {}
    sig_of: dict[str, tuple[str, str]] = {}

    def admit(expr: Any, outputs: tuple[Any, ...]) -> bool:
        key = (expr.result, E.digest(list(outputs)))
        sig_of[expr.identity] = key
        members = classes.setdefault(key, [])
        members.append(expr)
        if len(members) > 1:
            return False
        buckets.setdefault((expr.result, expr.nodes), []).append(expr)
        return True

    for item in spec["inputs"]:
        expr = E.Expr(item["type"], "input", name=item["name"])
        outputs = outcomes(E, expr, cases, primitives, limits)
        if outputs is not None:
            admit(expr, outputs)
    for constant in spec["constants"]:
        # v2 leaves constants as raw values; v3 normalizes them to {type, value}.
        if isinstance(constant, Mapping):
            expr = E.Expr(constant["type"], "constant", value=constant["value"])
        else:
            expr = E.Expr(E.infer_type(constant), "constant", value=constant)
        outputs = outcomes(E, expr, cases, primitives, limits)
        if outputs is not None:
            admit(expr, outputs)

    generated = 0
    candidates = 0
    winners: list[Any] = []
    stop_reason = "max_nodes"
    max_nodes = limits["max_nodes"]
    max_expressions = limits["max_expressions"]
    max_candidates = limits["max_candidates"]
    budget_hit = False

    def consider(expr: Any) -> bool:
        nonlocal generated, candidates, stop_reason, budget_hit
        generated += 1
        if generated > max_expressions:
            stop_reason = "max_expressions"
            budget_hit = True
            return False
        if expr.result == spec["output_type"]:
            if candidates >= max_candidates:
                stop_reason = "max_candidates"
                budget_hit = True
                return False
            candidates += 1
        outputs = outcomes(E, expr, cases, primitives, limits)
        if outputs is not None:
            if expr.result == spec["output_type"] and outputs == expected:
                winners.append(expr)
            admit(expr, outputs)
        return True

    for type_name in sorted(E.SUPPORTED_TYPES):
        for expr in list(buckets.get((type_name, 1), [])):
            if expr.result == spec["output_type"]:
                candidates += 1
                outputs = outcomes(E, expr, cases, primitives, limits)
                if outputs is not None and outputs == expected:
                    winners.append(expr)

    if not winners:
        for node_count in range(2, max_nodes + 1):
            for primitive in sorted(primitives.values(), key=lambda item: item.key):
                for sizes in E.compositions(node_count - 1, len(primitive.arguments)):
                    pools = [buckets.get((t, s), []) for t, s in zip(primitive.arguments, sizes)]
                    if any(not pool for pool in pools):
                        continue
                    for arguments in itertools.product(*pools):
                        if primitive.commutative and len(arguments) == 2 and arguments[0].identity > arguments[1].identity:
                            continue
                        expr = E.Expr(primitive.result, "call", primitive=primitive.key,
                                      arguments=tuple(arguments), nodes=node_count)
                        if not consider(expr):
                            break
                    if budget_hit:
                        break
                if budget_hit:
                    break
            if budget_hit:
                break
            if winners:
                stop_reason = "tier_complete_with_survivors"
                break

    expanded = expand_winners(E, winners, classes, sig_of, primitives, cases, expected, limits, max_variants)
    return {
        "winners_enumerated": len(winners),
        "survivors": expanded,
        "stop_reason": stop_reason,
        "generated_expressions": generated,
        "registered_candidates": candidates,
        "equivalence_classes": len(classes),
    }


def expand_winners(E: Any, winners: Sequence[Any], classes: Mapping[tuple[str, str], list[Any]],
                   sig_of: Mapping[str, tuple[str, str]], primitives: Mapping[str, Any],
                   cases: Sequence[Mapping[str, Any]], expected: tuple[Any, ...],
                   limits: Mapping[str, Any], max_variants: int) -> list[Any]:
    """Substitute observationally-equivalent subexpressions of the same size.

    Same size only. That keeps the recursion well-founded (arguments are always
    strictly smaller) and keeps the question honest: we are looking for equally
    minimal rivals, not for larger programs that also happen to fit.
    """
    memo: dict[str, list[Any]] = {}

    def alternates(expr: Any) -> list[Any]:
        key = sig_of.get(expr.identity)
        if key is None:
            return [expr]
        same_size = [m for m in classes.get(key, []) if m.nodes == expr.nodes]
        return same_size or [expr]

    def variants(expr: Any) -> list[Any]:
        if expr.identity in memo:
            return memo[expr.identity]
        memo[expr.identity] = [expr]
        produced: list[Any] = []
        seen: set[str] = set()
        for member in alternates(expr):
            if member.kind != "call":
                if member.identity not in seen:
                    seen.add(member.identity)
                    produced.append(member)
                continue
            pools = [variants(argument) for argument in member.arguments]
            for combination in itertools.product(*pools):
                rebuilt = E.Expr(member.result, "call", primitive=member.primitive,
                                 arguments=tuple(combination),
                                 nodes=1 + sum(item.nodes for item in combination))
                if rebuilt.identity not in seen:
                    seen.add(rebuilt.identity)
                    produced.append(rebuilt)
                if len(produced) >= max_variants:
                    break
            if len(produced) >= max_variants:
                break
        memo[expr.identity] = produced
        return produced

    survivors: list[Any] = []
    seen: set[str] = set()
    for winner in winners:
        for variant in variants(winner):
            if variant.identity in seen:
                continue
            outputs = outcomes(E, variant, cases, primitives, limits)
            if outputs is None or outputs != expected:
                continue
            seen.add(variant.identity)
            survivors.append(variant)
            if len(survivors) >= max_variants:
                return survivors
    return survivors


# --------------------------------------------------------------------------
# Probe generation
# --------------------------------------------------------------------------

SCALAR_CORPUS: dict[str, list[Any]] = {
    "int": [0, 1, -1, 2, -2, 3, 7, -7, 10],
    "float": [0.0, 1.0, -1.0, 0.5, -0.5, 2.5],
    "bool": [True, False],
    "str": ["", "a", "A", "ab", "aa", " a ", "Ab c"],
}


def type_corpus(E: Any, type_name: str, rng: random.Random, width: int) -> list[Any]:
    if type_name in SCALAR_CORPUS:
        return list(SCALAR_CORPUS[type_name])
    element = E.LIST_ELEMENT[type_name]
    base = SCALAR_CORPUS[element]
    values: list[Any] = [
        [],
        [base[0]],
        [base[1]],
        [base[0], base[0]],
        [base[0], base[1]],
        [base[1], base[0]],
        list(base[:3]),
        list(reversed(base[:3])),
    ]
    for _ in range(width):
        size = rng.randint(0, 4)
        values.append([rng.choice(base) for _ in range(size)])
    unique: list[Any] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def probe_inputs(E: Any, spec: Mapping[str, Any], rng: random.Random, sample: int) -> list[dict[str, Any]]:
    names = [item["name"] for item in spec["inputs"]]
    pools = {item["name"]: type_corpus(E, item["type"], rng, 6) for item in spec["inputs"]}
    existing = {E.canonical_json(case["inputs"]) for case in spec["cases"]}
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def offer(assignment: Mapping[str, Any]) -> None:
        key = E.canonical_json(assignment)
        if key in existing or key in seen:
            return
        seen.add(key)
        collected.append(copy.deepcopy(dict(assignment)))

    for values in itertools.product(*(pools[name] for name in names)):
        offer(dict(zip(names, values)))
        if len(collected) >= max(1, sample // 2):
            break
    guard = 0
    while len(collected) < sample and guard < sample * 20:
        guard += 1
        offer({name: rng.choice(pools[name]) for name in names})
    return collected


def outcome(E: Any, expr: Any, inputs: Mapping[str, Any], primitives: Mapping[str, Any],
            limits: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return {"ok": True, "value": evaluate_one(E, expr, inputs, primitives, limits)}
    except (ArithmeticError, IndexError, TypeError, ValueError, KeyError) as exc:
        return {"ok": False, "error": type(exc).__name__}


def refine(partition: Sequence[set[str]], column: Mapping[str, str]) -> list[set[str]]:
    result: list[set[str]] = []
    for group in partition:
        split: dict[str, set[str]] = {}
        for member in sorted(group):
            split.setdefault(column[member], set()).add(member)
        result.extend(split[key] for key in sorted(split))
    return result


def choose_probes(survivor_ids: Sequence[str], table: Sequence[Mapping[str, str]],
                  max_probes: int) -> tuple[list[int], list[set[str]]]:
    partition: list[set[str]] = [set(survivor_ids)]
    chosen: list[int] = []
    used: set[int] = set()
    while len(partition) < len(survivor_ids) and len(chosen) < max_probes:
        best_index = None
        best_gain = 0
        best_partition: list[set[str]] = []
        for index in range(len(table)):
            if index in used:
                continue
            refined = refine(partition, table[index])
            gain = len(refined) - len(partition)
            if gain > best_gain:
                best_index, best_gain, best_partition = index, gain, refined
        if best_index is None:
            break
        used.add(best_index)
        chosen.append(best_index)
        partition = best_partition
    return chosen, partition


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def survivor_id(E: Any, expr: Any) -> str:
    return "s-" + E.digest(expr.document())[:12]


def command_survivors(E: Any, args: argparse.Namespace) -> dict[str, Any]:
    raw = E.load_json(args.construction)
    spec = E.validate_spec(raw)
    primitives = E.focused_primitives(spec)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    ledger = make_ledger(E, output / "probe-ledger.jsonl", spec["limits"])
    # Named EXPERIMENT_STARTED because the v3 engine's ledger verifier requires
    # that opening event; the phase field keeps the two ledgers distinguishable.
    ledger.append("EXPERIMENT_STARTED", {
        "phase": "enumeration",
        "construction_spec_digest": E.digest(spec),
        "construction_case_count": len(spec["cases"]),
        "primitive_count": len(primitives),
        "max_variants": args.max_variants,
    })
    found = enumerate_survivors(E, spec, primitives, spec["limits"], args.max_variants)
    survivors = []
    for expr in found["survivors"]:
        identifier = survivor_id(E, expr)
        survivors.append({"id": identifier, "node_count": expr.nodes, "program": expr.document()})
        ledger.append("SURVIVOR_REGISTERED", {
            "survivor_id": identifier,
            "node_count": expr.nodes,
            "artifact_digest": E.digest(expr.document()),
        })
    document = {
        "schema": SURVIVORS_SCHEMA,
        "construction_spec_digest": E.digest(spec),
        "construction": raw,
        "function_name": spec["function_name"],
        "inputs": spec["inputs"],
        "output_type": spec["output_type"],
        "survivor_count": len(survivors),
        "survivors": survivors,
        "search": {
            "stop_reason": found["stop_reason"],
            "winners_enumerated": found["winners_enumerated"],
            "generated_expressions": found["generated_expressions"],
            "registered_candidates": found["registered_candidates"],
            "equivalence_classes": found["equivalence_classes"],
        },
        "determination": determination(len(survivors)),
    }
    ledger.append("ENUMERATION_CLOSED", {
        "survivor_count": len(survivors),
        "stop_reason": found["stop_reason"],
        "determination": document["determination"],
    })
    E.atomic_json(output / "survivors.json", document)
    return document


def determination(count: int) -> str:
    if count == 0:
        return "NO_SURVIVOR"
    if count == 1:
        return "UNIQUE"
    return "UNDERDETERMINED"


def command_probes(E: Any, args: argparse.Namespace) -> dict[str, Any]:
    document = E.load_json(args.survivors)
    if document.get("schema") != SURVIVORS_SCHEMA:
        raise DiscriminationError(f"survivors file schema must be {SURVIVORS_SCHEMA}")
    spec = E.validate_spec(document["construction"])
    primitives = E.focused_primitives(spec)
    survivors = document["survivors"]
    if len(survivors) < 2:
        raise DiscriminationError(
            "probe generation needs at least two survivors; "
            f"this run is {document.get('determination')}"
        )
    programs = {
        item["id"]: E.expression_from_document(item["program"], E.primitive_library())
        for item in survivors
    }
    rng = random.Random(args.seed)
    inputs = probe_inputs(E, spec, rng, args.sample)
    table: list[dict[str, str]] = []
    for assignment in inputs:
        column = {
            identifier: E.canonical_json(outcome(E, expr, assignment, primitives, spec['limits']))
            for identifier, expr in programs.items()
        }
        table.append(column)
    identifiers = [item["id"] for item in survivors]
    chosen, partition = choose_probes(identifiers, table, args.max_probes)
    probes = []
    for order, index in enumerate(chosen, 1):
        predictions = []
        for identifier in identifiers:
            result = outcome(E, programs[identifier], inputs[index], primitives, spec['limits'])
            predictions.append({
                "survivor_id": identifier,
                "predicted": result["value"] if result["ok"] else None,
                "evaluates": result["ok"],
                "error": result.get("error"),
            })
        probes.append({
            "id": f"p{order:03d}",
            "inputs": inputs[index],
            "predictions": predictions,
        })
    unresolved = [sorted(group) for group in partition if len(group) > 1]
    result = {
        "schema": PROBES_SCHEMA,
        "construction_spec_digest": document["construction_spec_digest"],
        "function_name": document["function_name"],
        "survivor_count": len(survivors),
        "sampled_inputs": len(inputs),
        "probe_count": len(probes),
        "probes": probes,
        "separates_all_survivors": not unresolved,
        "indistinguishable_groups": unresolved,
    }
    E.atomic_json(args.output, result)
    return result


def command_resolve(E: Any, args: argparse.Namespace) -> dict[str, Any]:
    survivors_document = E.load_json(args.survivors)
    probes_document = E.load_json(args.probes)
    answers_document = E.load_json(args.answers)
    if survivors_document.get("schema") != SURVIVORS_SCHEMA:
        raise DiscriminationError(f"survivors file schema must be {SURVIVORS_SCHEMA}")
    if probes_document.get("schema") != PROBES_SCHEMA:
        raise DiscriminationError(f"probes file schema must be {PROBES_SCHEMA}")
    if answers_document.get("schema") != ANSWERS_SCHEMA:
        raise DiscriminationError(f"answers file schema must be {ANSWERS_SCHEMA}")
    if probes_document["construction_spec_digest"] != survivors_document["construction_spec_digest"]:
        raise DiscriminationError("probes were generated from a different construction spec")
    authority = answers_document.get("authority")
    if not isinstance(authority, str) or not authority.strip():
        raise DiscriminationError("answers file must name the authority that supplied the outputs")

    spec = E.validate_spec(survivors_document["construction"])
    output_type = spec["output_type"]
    by_id = {probe["id"]: probe for probe in probes_document["probes"]}
    answers: dict[str, Any] = {}
    for item in answers_document.get("answers", []):
        probe_id = item.get("probe_id")
        if probe_id not in by_id:
            raise DiscriminationError(f"answer references unknown probe: {probe_id}")
        if not E.valid_value(item.get("expected"), output_type):
            raise DiscriminationError(f"{probe_id}: expected value is not {output_type}")
        answers[probe_id] = item["expected"]
    if not answers:
        raise DiscriminationError("no answers supplied; resolution requires at least one")

    remaining = []
    for item in survivors_document["survivors"]:
        identifier = item["id"]
        eliminated_by = None
        for probe_id, expected in sorted(answers.items()):
            prediction = next(
                entry for entry in by_id[probe_id]["predictions"] if entry["survivor_id"] == identifier
            )
            if not prediction["evaluates"] or prediction["predicted"] != expected:
                eliminated_by = probe_id
                break
        if eliminated_by is None:
            remaining.append(item)

    verdict = determination(len(remaining))
    resolved_cases = [
        {"id": f"probe-{probe_id}", "inputs": by_id[probe_id]["inputs"], "expected": answers[probe_id]}
        for probe_id in sorted(answers)
    ]
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    ledger = make_ledger(E, output / "resolution-ledger.jsonl", spec["limits"])
    ledger.append("EXPERIMENT_STARTED", {
        "phase": "resolution",
        "construction_spec_digest": survivors_document["construction_spec_digest"],
        "authority": authority,
        "answered_probes": sorted(answers),
        "survivor_count": len(survivors_document["survivors"]),
    })
    for item in remaining:
        ledger.append("SURVIVOR_RETAINED", {"survivor_id": item["id"]})
    ledger.append("RESOLUTION_CLOSED", {"verdict": verdict, "remaining": len(remaining)})

    augmented = copy.deepcopy(survivors_document["construction"])
    augmented["cases"] = list(augmented.get("cases", [])) + resolved_cases
    E.validate_spec(augmented)
    E.atomic_json(output / "augmented-construction.json", augmented)

    result = {
        "schema": RESOLUTION_SCHEMA,
        "verdict": verdict,
        "authority": authority,
        "construction_spec_digest": survivors_document["construction_spec_digest"],
        "answered_probes": len(answers),
        "survivors_before": len(survivors_document["survivors"]),
        "survivors_after": len(remaining),
        "remaining": remaining,
        "resolved_cases": resolved_cases,
        "augmented_construction": str((output / "augmented-construction.json").resolve()),
        "guidance": {
            "UNIQUE": "Re-run the synthesis engine on augmented-construction.json, then qualify and retain as usual.",
            "UNDERDETERMINED": "Generate further probes, or supply cases that separate the remaining survivors.",
            "NO_SURVIVOR": "The authority excludes every enumerated program. The target is outside this grammar or node budget; widen the vocabulary or max_nodes rather than re-running unchanged.",
        }[verdict],
    }
    E.atomic_json(output / "resolution.json", result)
    return result


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

SELF_TEST_CONSTRUCTION = {
    "schema": None,  # filled from the loaded engine's SCHEMA, so either version can run it
    "function_name": "count_positive",
    "mode": "focused",
    "inputs": [{"name": "values", "type": "list_int"}],
    "output_type": "int",
    "constants": [0],
    "primitives": ["each_greater", "each_greater_equal", "select", "length"],
    "limits": {"max_nodes": 6, "max_expressions": 200000, "max_candidates": 50000},
    "cases": [
        {"id": "c1", "inputs": {"values": [-2, 3, 4]}, "expected": 2},
        {"id": "c2", "inputs": {"values": [-3, -1]}, "expected": 0},
        {"id": "c3", "inputs": {"values": [5]}, "expected": 1},
    ],
}


def walk(program: Mapping[str, Any]) -> list[dict[str, Any]]:
    nodes = [dict(program)]
    for argument in program.get("arguments", []):
        nodes.extend(walk(argument))
    return nodes


def command_self_test(E: Any, args: argparse.Namespace) -> dict[str, Any]:
    import tempfile

    def check(condition: bool, message: str) -> None:
        if not condition:
            raise DiscriminationError(f"self-test failed: {message}")

    with tempfile.TemporaryDirectory(prefix="dcp-self-test-") as temporary:
        root = Path(temporary)
        construction = root / "construction.json"
        spec = dict(SELF_TEST_CONSTRUCTION, schema=E.SCHEMA)
        E.atomic_json(construction, spec)

        found = command_survivors(E, argparse.Namespace(
            construction=construction, output=root / "run", max_variants=200))
        check(found["determination"] == "UNDERDETERMINED",
              f"expected an underdetermined observation, got {found['determination']}")
        check(found["survivor_count"] == 2,
              f"expected exactly two equally minimal survivors, got {found['survivor_count']}")
        used = {
            node["primitive"]
            for item in found["survivors"]
            for node in walk(item["program"])
            if node["kind"] == "call"
        }
        check("each_greater:list_int,int->list_bool" in used and
              "each_greater_equal:list_int,int->list_bool" in used,
              "survivors did not include both the strict and non-strict boundary program")

        probes = command_probes(E, argparse.Namespace(
            survivors=root / "run" / "survivors.json", output=root / "probes.json",
            max_probes=8, sample=200, seed=7))
        check(probes["separates_all_survivors"], "probes did not separate the survivors")
        check(probes["probe_count"] >= 1, "no discriminating probe was produced")
        for probe in probes["probes"]:
            check(any(0 in value for value in probe["inputs"].values() if isinstance(value, list)),
                  "the separating probe should exercise the untested zero boundary")

        answers = {
            "schema": ANSWERS_SCHEMA,
            "authority": "self-test-oracle-count-strictly-positive",
            "answers": [
                {"probe_id": probe["id"],
                 "expected": sum(1 for value in probe["inputs"]["values"] if value > 0)}
                for probe in probes["probes"]
            ],
        }
        answers_path = root / "answers.json"
        E.atomic_json(answers_path, answers)

        resolution = command_resolve(E, argparse.Namespace(
            survivors=root / "run" / "survivors.json", probes=root / "probes.json",
            answers=answers_path, output=root / "resolved"))
        check(resolution["verdict"] == "UNIQUE",
              f"expected a unique survivor after resolution, got {resolution['verdict']}")
        winner = resolution["remaining"][0]["program"]
        winner_primitives = {node["primitive"] for node in walk(winner) if node["kind"] == "call"}
        check("each_greater:list_int,int->list_bool" in winner_primitives,
              "resolution kept the wrong boundary program")
        check("each_greater_equal:list_int,int->list_bool" not in winner_primitives,
              "resolution failed to eliminate the non-strict boundary program")

        rerun = command_survivors(E, argparse.Namespace(
            construction=Path(resolution["augmented_construction"]),
            output=root / "rerun", max_variants=200))
        check(rerun["determination"] == "UNIQUE",
              f"augmented construction is still ambiguous: {rerun['determination']}")

        wrong_answers = {
            "schema": ANSWERS_SCHEMA,
            "authority": "self-test-negative-control",
            "answers": [{"probe_id": probe["id"], "expected": 99} for probe in probes["probes"]],
        }
        wrong_path = root / "wrong-answers.json"
        E.atomic_json(wrong_path, wrong_answers)
        negative = command_resolve(E, argparse.Namespace(
            survivors=root / "run" / "survivors.json", probes=root / "probes.json",
            answers=wrong_path, output=root / "negative"))
        check(negative["verdict"] == "NO_SURVIVOR",
              "an authority inconsistent with every candidate must eliminate all survivors")

        for name in ("run/probe-ledger.jsonl", "resolved/resolution-ledger.jsonl"):
            status = E.verify_ledger(root / name)
            check(status["status"] == "VALID", f"{name} did not verify")

    return {"status": "PASS", "checks": "survivors, probes, resolution, augmentation, negative control, ledgers"}


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", type=Path, default=None,
                        help="path to synthesize-verified-code/scripts/synthesize.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    survivors = subparsers.add_parser("survivors", help="enumerate every equally minimal fitting program")
    survivors.add_argument("--construction", required=True, type=Path)
    survivors.add_argument("--output", required=True, type=Path)
    survivors.add_argument("--max-variants", type=int, default=500, dest="max_variants")

    probes = subparsers.add_parser("probes", help="generate inputs on which the survivors disagree")
    probes.add_argument("--survivors", required=True, type=Path)
    probes.add_argument("--output", required=True, type=Path)
    probes.add_argument("--max-probes", type=int, default=8, dest="max_probes")
    probes.add_argument("--sample", type=int, default=400)
    probes.add_argument("--seed", type=int, default=7)

    resolve = subparsers.add_parser("resolve", help="eliminate survivors using authority answers")
    resolve.add_argument("--survivors", required=True, type=Path)
    resolve.add_argument("--probes", required=True, type=Path)
    resolve.add_argument("--answers", required=True, type=Path)
    resolve.add_argument("--output", required=True, type=Path)

    subparsers.add_parser("self-test", help="run a dependency-free end-to-end test")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        engine = load_engine(args.engine)
        handler = {
            "survivors": command_survivors,
            "probes": command_probes,
            "resolve": command_resolve,
            "self-test": command_self_test,
        }[args.command]
        result = handler(engine, args)
    except DiscriminationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # engine errors carry their own messages
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
