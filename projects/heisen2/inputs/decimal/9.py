from decimal import Decimal, getcontext
getcontext().Emin = -999999999
Decimal("1e-999999999")