def fibonacci():
    a, b = 0, 1
    while True:
        yield a
        a, b = b, a + b

def take(n, it):
    for _, v in zip(range(n), it):
        yield v

gen = (x * 2 for x in range(5))
fibs = list(take(8, fibonacci()))
