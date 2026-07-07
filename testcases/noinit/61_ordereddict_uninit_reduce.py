import collections
import copy
d = collections.OrderedDict.__new__(collections.OrderedDict)
d.__reduce__()
copy.copy(d)
