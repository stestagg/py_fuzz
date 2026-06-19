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

# 02 OrderedDict.copy: key __hash__ removes current key
od=OrderedDict(); k=K(lambda: od.pop(k,None)); od[k]=0; od[0]=1; go(od.copy)
