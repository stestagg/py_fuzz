from decimal import *
c = getcontext()
c.traps[FloatOperation] = True
Decimal(3.141592653589793)