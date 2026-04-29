# 9. Collation callback with weird comparisons / exceptions
import sqlite3
def coll(a, b):
    if "\x00" in a + b:
        raise RuntimeError("nul")
    return (a[::-1] > b[::-1]) - (a[::-1] < b[::-1])
con = sqlite3.connect(":memory:")
con.create_collation("C", coll)
try:
    con.execute("select 'a'||char(0) collate C < 'b'").fetchall()
except Exception:
    pass
