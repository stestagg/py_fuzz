# 15. load_extension with empty path — error handling in wrapper
import sqlite3

con = sqlite3.connect(":memory:")
try:
    con.enable_load_extension(True)
    con.load_extension("")
except Exception:
    pass
finally:
    try:
        con.enable_load_extension(False)
    except Exception:
        pass
