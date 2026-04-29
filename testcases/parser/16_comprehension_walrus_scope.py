# Comprehension with multiple for/if clauses, walrus crossing scope boundary
out = [
    (a, b, c)
    for a in range(10)
    if (sa := a * a) > 5
    for b in range(a)
    if (sb := b * b) < sa
    for c in range(b)
    if (sc := a + b + c) == (sa - sb)
]

# walrus in nested genexpr used as iterable
processed = list(
    result
    for chunk in data
    if (n := len(chunk)) > 0
    for result in (transform(x, n) for x in chunk if x)
)
