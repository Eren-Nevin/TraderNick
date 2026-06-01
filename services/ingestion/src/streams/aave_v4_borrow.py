"""Stream worker: aave_v4.borrow."""
import asyncio
from groups.aave_v4_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["borrow"], stream_name="aave_v4.borrow"))
