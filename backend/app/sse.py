import asyncio


class SSEBroadcaster:
    def __init__(self):
        self._queues: list[asyncio.Queue] = []

    async def subscribe(self):
        queue = asyncio.Queue()
        self._queues.append(queue)
        try:
            while True:
                data = await queue.get()
                yield data
        finally:
            self._queues.remove(queue)

    async def publish(self, data: str):
        for queue in self._queues:
            await queue.put(data)


broadcaster = SSEBroadcaster()
