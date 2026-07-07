import collections
d = collections.OrderedDict.__new__(collections.OrderedDict)
repr(d)
d["a"] = 1
d.move_to_end("a")
d.popitem()
