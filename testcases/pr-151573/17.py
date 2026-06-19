from collections import OrderedDict, deque
import heapq, pickle

armed = False
busy = False

def fire(f):
    global busy
    if armed and not busy:
        busy = True
        try:
            f()
        finally:
            busy = False

class K:
    def __init__(self, f=lambda: None, h=1):
        self.f = f
        self.h = h
    def __hash__(self):
        fire(self.f)
        return self.h
    def __eq__(self, other):
        fire(self.f)
        return True
    def __lt__(self, other):
        fire(self.f)
        return False
    def __repr__(self):
        fire(self.f)
        return "K"
    def __reduce__(self):
        fire(self.f)
        return (int, (1,))

def go(f):
    global armed, busy
    try:
        armed = True
        busy = False
        f()
    except BaseException:
        pass
    finally:
        armed = False
        busy = False

# 17 list.remove: element __eq__ clears list during scan
a=[K(lambda: a.clear()),1]; go(lambda: a.remove(K()))
