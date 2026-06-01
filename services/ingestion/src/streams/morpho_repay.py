"""Stream worker: morpho.repay."""
import asyncio
from groups.morpho_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["repay"], stream_name="morpho.repay"))
