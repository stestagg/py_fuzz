import sys
lazy import json
sys.modules["json"] = None
try:
    json.dumps({})
except Exception:
    pass
