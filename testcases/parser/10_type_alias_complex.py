# PEP 695: type alias statement with complex RHS including unions and generics
type Vector[T] = list[T]
type Matrix[T] = list[list[T]]
type MaybeNested[T] = T | list[T] | dict[str, T] | None
type Callback[**P, R] = collections.abc.Callable[P, R]
type Recursive = int | list["Recursive"]
type Union3 = int | str | bytes | None | list[int | str]
