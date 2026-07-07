import collections
d = collections.deque([1, 2, 3, 4, 5])
d.__init__([6, 7, 8], 2)
list(d)
d.__init__()
d.append(9)
list(d)
