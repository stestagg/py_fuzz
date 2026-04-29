# PEP 604: X | Y union annotations, nested generics, forward references
def f(x: int | str | None) -> bool | None: ...
def g(x: list[int | str] | dict[str, int | None]) -> tuple[int, ...] | None: ...
def h(x: "int | str") -> "list[int] | None": ...

x: int | str = 0
y: list[int | str | bytes | None] | dict[str, list[int] | None] | None = None

class C:
    attr: "C | None" = None
    items: list["C"] | tuple["C", ...] = ()
