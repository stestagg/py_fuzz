lazy import json
g = globals()
x = g["json"]
del json
x.dumps({})
