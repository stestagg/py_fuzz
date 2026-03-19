import asyncio

async def fetch(url):
    await asyncio.sleep(0)
    return url

async def main():
    tasks = [fetch(f"url{i}") for i in range(3)]
    results = await asyncio.gather(*tasks)
    return results

async def aiter_example():
    async def agen():
        for i in range(3):
            yield i
    return [x async for x in agen()]
