import decimal
c = decimal.Context.__new__(decimal.Context)
repr(c)
c.clear_flags()
c.create_decimal("1.5")
