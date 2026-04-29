# 4. text_factory returning non-str objects
import sqlite3
con = sqlite3.connect(":memory:")
con.text_factory = lambda b: memoryview(b)
con.execute("select 'abc', char(0), 'xyz'").fetchall()
