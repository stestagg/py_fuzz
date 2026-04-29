# yield from, (yield x) as expression, yield in complex positions
def gen1():
    x = yield 1
    y = (yield x + 1)
    z = (yield from subgen())
    return x, y, z

def gen2():
    result = yield from (x * 2 for x in range(10) if (yield x))

def gen3():
    (yield (yield (yield 0)))

def gen4():
    a = b = (yield 1)
    [c, d] = (yield [1, 2])
    *e, f = (yield range(5))
