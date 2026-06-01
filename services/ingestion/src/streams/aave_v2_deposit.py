"""Stream worker: aave_v2.deposit."""
import asyncio
from groups.aave_v2_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["deposit"], stream_name="aave_v2.deposit"))
