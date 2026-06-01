"""Stream worker: uniswap_v3.swap."""
import asyncio
from groups.uniswap_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["swap"], stream_name="uniswap_v3.swap"))
