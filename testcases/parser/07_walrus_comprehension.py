# PEP 572: walrus in comprehension filter, outer scope capture, nested comprehensions
results = [y := f(x), y**2, y**3]
filtered = [y for x in data if (y := transform(x)) is not None]
nested = [[z for z in row if (z := z * 2) > 0] for row in matrix if (s := sum(row)) > 0]
any_found = any((match := item) for item in seq if item > 0)
