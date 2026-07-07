import sqlite3
c = sqlite3.Connection.__new__(sqlite3.Connection)
c.row_factory = sqlite3.Row
c.execute("select 1")
