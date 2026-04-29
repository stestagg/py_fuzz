# 14. enable_load_extension toggle — wrapper path even when disabled at build time
import sqlite3

con = sqlite3.connect(":memory:")
try:
    con.enable_load_extension(True)
    con.enable_load_extension(False)
except Exception:
    pass
