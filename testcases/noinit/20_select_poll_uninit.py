import select
p = select.poll.__new__(select.poll)
p.register(0)
p.poll(0)
