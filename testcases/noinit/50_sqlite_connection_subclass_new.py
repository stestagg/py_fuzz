import sqlite3
class C(sqlite3.Connection):
    pass
c = C.__new__(C)
repr(c)
c.execute("select 1")
c.cursor()
