import asyncio
t = asyncio.Task.__new__(asyncio.Task)
repr(t)
t.get_name()
t.cancel()
t.result()
