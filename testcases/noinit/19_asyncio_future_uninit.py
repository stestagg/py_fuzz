import asyncio
f = asyncio.Future.__new__(asyncio.Future)
repr(f)
f.set_result(1)
f.result()
