import collections
d = collections.defaultdict.__new__(collections.defaultdict)
repr(d)
d.default_factory
d["missing"]
