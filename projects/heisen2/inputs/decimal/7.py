from decimal import *
c = getcontext()
c.traps[DivisionByZero] = True
Decimal(1) / Decimal(0)