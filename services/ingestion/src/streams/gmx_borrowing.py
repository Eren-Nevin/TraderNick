"""Stream worker: gmx.borrowing."""
import asyncio
from groups.gmx_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["borrowing"], stream_name="gmx.borrowing"))
