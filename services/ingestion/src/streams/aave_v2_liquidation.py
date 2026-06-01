"""Stream worker: aave_v2.liquidation."""
import asyncio
from groups.aave_v2_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["liquidation"], stream_name="aave_v2.liquidation"))
