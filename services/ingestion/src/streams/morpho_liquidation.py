"""Stream worker: morpho.liquidation."""
import asyncio
from groups.morpho_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["liquidation"], stream_name="morpho.liquidation"))
