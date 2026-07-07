import contextvars
c = contextvars.Context.__new__(contextvars.Context)
repr(c)
list(c)
len(c)
c.copy()
