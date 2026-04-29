# PEP 654: except* with multiple types, nested try/except* blocks
try:
    pass
except* (ValueError, TypeError) as eg:
    pass
except* KeyError as eg:
    pass

try:
    try:
        pass
    except* RuntimeError as inner:
        raise
except* (OSError, IOError) as outer:
    pass
finally:
    pass
