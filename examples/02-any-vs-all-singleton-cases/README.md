# Example 2: any versus all, hidden by singleton cases

Decide whether a list contains a negative value. Every construction case is a
single-element list.

## The ambiguity

On a one-element list, "any element is negative" and "all elements are
negative" are the same question. Both programs fit, at four nodes each:

```text
any(each_less(values, 0))
all(each_less(values, 0))
```

The qualification cases are singletons too, so both pass that gate as well.
This is a case-shape blind spot rather than a value blind spot: no single case
is wrong, but the whole set is built from inputs on which the two readings
cannot come apart.

## Running it

```bash
python .../discriminate.py survivors --construction construction.json --output run
```

```text
determination: UNDERDETERMINED
survivor_count: 2
  all(each_less(values, 0))
  any(each_less(values, 0))
```

```bash
python .../discriminate.py probes --survivors run/survivors.json --output probes.json
```

```text
probe_count: 1
p001  values=[]   all(...) predicts true   any(...) predicts false
```

The greedy selector picked the empty list. That single probe separates the
survivors and exercises two untested conditions at once, since `all` over an
empty list is vacuously true while `any` is false. A multi-element mixed list
such as `[1, -1]` would also have separated them; the empty list does it with a
smaller input.

```bash
python .../discriminate.py resolve \
  --survivors run/survivors.json --probes probes.json \
  --answers answers.json --output resolved
```

```text
verdict: UNIQUE   2 -> 1
winner: any(each_less(values, 0))
```

## The point

Example 1 hides its ambiguity in the *values* the cases use. This one hides it
in the *shape* of the cases. Both slip past a held-out qualification file
whenever that file was written with the same instincts as the construction
cases, which is the normal situation when one person writes both.

Answering `false` for the empty list picks `any`. Answering `true` picks `all`.
The authority states which reading of "has a negative" was meant, rather than
the enumeration order guessing.
