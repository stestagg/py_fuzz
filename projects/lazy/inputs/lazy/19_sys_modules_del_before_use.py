import sys
lazy import json
del sys.modules["json"]
json.dumps({})
