"""Stream worker: uniswap_v4.deposit."""
import asyncio
from groups.uniswap_v4_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["deposit"], stream_name="uniswap_v4.deposit"))
