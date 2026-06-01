"""Stream worker: uniswap_v2.collect."""
import asyncio
from groups.uniswap_v2_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["collect"], stream_name="uniswap_v2.collect"))
