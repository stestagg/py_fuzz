import collections
import copy
d = collections.deque.__new__(collections.deque)
d.__reduce__()
copy.copy(d)
