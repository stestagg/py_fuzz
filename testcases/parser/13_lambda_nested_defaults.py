# Lambda in complex positions: nested, as default arg, in annotation default
f = lambda x, y=lambda z: z*2: x + y(x)
g = lambda *args, key=lambda x: x[0], **kw: sorted(args, key=key)
h = lambda f=lambda: (lambda: 0): f()()

def annotated(
    x: int,
    transform=lambda v, scale=10: v * scale,
    combine=lambda a, b: a + b,
) -> int: ...

make = lambda cls, *bases, **ns: type(cls, bases, ns)
