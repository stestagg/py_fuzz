# PEP 701 f-string: nested expressions, calls, ternary, format spec
x = 42
result = f"{x!r:>{x}}" + f"{'hi' if x > 0 else 'lo'}" + f"{len(str(x)) + 1}"
multi = f"{x:{'.3f' if x > 1 else 'd'}}"
