from decimal import *
c = Context(prec=3, Emax=3, Emin=-3)
c.traps[Overflow] = True
c.create_decimal("9.99e999999") * c.create_decimal("9.99e999999")