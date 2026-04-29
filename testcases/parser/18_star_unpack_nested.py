# Star expressions in assignment targets, returns, yields, function calls
[*a, b] = [1, 2, 3, 4]
(*x, y, z) = range(5)
[a, *b, c] = seq
first, *rest = iterable
*init, last = iterable

def f():
    return *a, *b, c

def g():
    yield *items, extra

result = [*list1, *list2, extra]
merged = {**dict1, **dict2, "key": val}
call(*args1, *args2, kw=val, **kw1, **kw2)
