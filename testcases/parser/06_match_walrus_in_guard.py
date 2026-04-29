# PEP 634 + PEP 572: walrus operator inside match guards and pattern expressions
def f(v):
    match v:
        case [x, y] if (s := x + y) > 10:
            pass
        case {"n": int() as n} if (sq := n * n) and sq < 1000:
            pass
        case str() as s if (parts := s.split()) and len(parts) > 1:
            pass
