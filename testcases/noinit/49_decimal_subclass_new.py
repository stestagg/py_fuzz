import decimal
class D(decimal.Decimal):
    __slots__ = ()
d = D.__new__(D)
repr(d)
d.as_tuple()
d + 1
