# PEP 634: match/case OR-patterns with class patterns, literals, and guards
def f(v):
    match v:
        case 0 | 1 | 2:
            pass
        case str() | bytes() | bytearray() if len(v) > 0:
            pass
        case [x, y] | (x, y) if x == y:
            pass
        case {"key": int() | float() as n} if n > 0:
            pass
        case _:
            pass
