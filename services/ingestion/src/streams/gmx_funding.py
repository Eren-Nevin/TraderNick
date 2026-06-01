"""Stream worker: gmx.funding."""
import asyncio
from groups.gmx_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["funding"], stream_name="gmx.funding"))
