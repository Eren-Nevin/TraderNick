"""Stream worker: morpho.withdraw."""
import asyncio
from groups.morpho_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["withdraw"], stream_name="morpho.withdraw"))
