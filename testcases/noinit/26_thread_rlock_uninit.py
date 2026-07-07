import _thread
r = _thread.RLock.__new__(_thread.RLock)
repr(r)
r.acquire(False)
r.release()
