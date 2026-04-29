# 2. register_adapter returning unusual Python type
import sqlite3
class X: pass
sqlite3.register_adapter(X, lambda v: memoryview(b"abc\x00def"))
con = sqlite3.connect(":memory:")
con.execute("select ?", (X(),)).fetchall()
