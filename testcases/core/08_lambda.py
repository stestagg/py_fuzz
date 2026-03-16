add = lambda x, y: x + y
identity = lambda x: x
compose = lambda f, g: lambda x: f(g(x))

nums = [3, 1, 4, 1, 5, 9]
nums.sort(key=lambda x: -x)
pairs = sorted([(1, 'b'), (2, 'a')], key=lambda p: p[1])
