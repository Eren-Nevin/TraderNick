"""Stream worker: morpho.supply."""
import asyncio
from groups.morpho_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["supply"], stream_name="morpho.supply"))
