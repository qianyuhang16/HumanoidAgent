"""Close every owned resource before propagating an error or cancellation."""

import asyncio


async def close_all(*resources):
    pending = asyncio.gather(*(resource.close() for resource in resources), return_exceptions=True)
    try:
        results = await asyncio.shield(pending)
    except asyncio.CancelledError:
        await pending
        raise
    for result in results:
        if isinstance(result, BaseException):
            raise result
