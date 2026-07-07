import select
k = select.kqueue.__new__(select.kqueue)
repr(k)
k.fileno()
k.control([], 0, 0)
