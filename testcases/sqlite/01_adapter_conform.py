# 1. Adapter + __conform__ path (prepare_protocol / microprotocols)
import sqlite3
class X:
    def __conform__(self, protocol):
        return b"\x00\xffA"
con = sqlite3.connect(":memory:")
con.execute("select ?", (X(),)).fetchall()
