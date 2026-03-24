from _decimal import *
getcontext().prec = 50
format(Decimal("NaN123"), "N")