import queue
q = queue.SimpleQueue.__new__(queue.SimpleQueue)
q.put(1)
q.get_nowait()
q.qsize()
