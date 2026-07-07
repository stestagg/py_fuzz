import sqlite3
c = sqlite3.connect(":memory:")
c.execute("create table t(x)")
c.__init__(":memory:")
c.execute("select 1")
