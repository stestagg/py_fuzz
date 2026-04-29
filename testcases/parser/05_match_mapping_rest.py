# PEP 634: mapping patterns with **rest, nested sequence/class/mapping combos
def f(v):
    match v:
        case {"a": [int() as x, *rest], "b": {"c": str() as s}, **extra}:
            pass
        case {"x": Point(x=int(), y=float()), **rest2}:
            pass
        case [{"key": val}, *_, {"key": val2}] if val == val2:
            pass
