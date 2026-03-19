from typing import TypeVar, Generic

type Vector = list[float]
type Matrix = list[Vector]

T = TypeVar('T')

class Stack(Generic[T]):
    def __init__(self) -> None:
        self._items: list[T] = []

    def push(self, item: T) -> None:
        self._items.append(item)

    def pop(self) -> T:
        return self._items.pop()

type Callback[R] = (int, str) -> R
