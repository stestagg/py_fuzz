# 10. Blob API + slice assignment / bounds handling
import sqlite3
con = sqlite3.connect(":memory:")
con.execute("create table t(x blob)")
con.execute("insert into t values (zeroblob(4))")
with con.blobopen("t", "x", 1) as b:
    b[0] = 65
    b[1:4] = b"\x00\xffZ"
    bytes(b)
