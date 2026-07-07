import itertools
class C(itertools.count):
    pass
c = C.__new__(C)
repr(c)
next(c)
