# PEP 572: walrus in nested call args, ternary chains, boolean short-circuit
x = foo(a := bar(), b := baz(a), c := a + b)
y = (n := len(data)) and (avg := total / n) and avg > threshold
z = (v := compute()) if condition else (v := default())
w = [f(x) for x in seq if (r := g(x)) and (s := h(r)) > 0 and s < 100]
