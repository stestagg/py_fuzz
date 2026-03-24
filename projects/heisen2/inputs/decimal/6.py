from decimal import Decimal, getcontext
c = getcontext()
c.prec = 1
Decimal(1) / Decimal(3)