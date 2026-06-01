"""Stream worker: morpho.borrow."""
import asyncio
from groups.morpho_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["borrow"], stream_name="morpho.borrow"))
