# Async grammar: async for in comprehension, await in genexpr, nested async
async def f():
    result = [x async for x in aiter if x > 0]
    gen = (await coro(x) async for x in aiter)
    nested = {k: [v async for v in vals] async for k, vals in mapping}
    flat = [y for x in data async for y in aiter(x) if await check(y)]
    return result

async def g():
    async with ctx() as c:
        async for item in c:
            yield item
