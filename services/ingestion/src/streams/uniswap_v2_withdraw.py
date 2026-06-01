"""Stream worker: uniswap_v2.withdraw."""
import asyncio
from groups.uniswap_v2_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["withdraw"], stream_name="uniswap_v2.withdraw"))
