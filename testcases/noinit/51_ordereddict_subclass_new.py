import collections
class D(collections.OrderedDict):
    pass
d = D.__new__(D)
d["a"] = 1
d.move_to_end("a")
d.popitem()
