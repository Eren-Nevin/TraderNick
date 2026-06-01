"""Stream worker: aave_v4.liquidation."""
import asyncio
from groups.aave_v4_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["liquidation"], stream_name="aave_v4.liquidation"))
