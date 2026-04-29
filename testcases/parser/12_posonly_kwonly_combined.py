# Complex function signature: positional-only (/), *args, keyword-only, **kwargs with annotations
def f(
    a: int,
    b: str = "x",
    /,
    c: float = 1.0,
    *args: tuple[int, ...],
    d: bool = True,
    e: list[str] | None = None,
    **kwargs: dict[str, object],
) -> tuple[int, str] | None: ...

def g(x: int, /, y: int = 0, *, z: int) -> int: ...
def h(a, b, /, c, d, *e, f, g=1, **h): ...
