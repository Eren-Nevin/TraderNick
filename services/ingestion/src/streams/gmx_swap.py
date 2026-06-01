"""Stream worker: gmx.swap."""
import asyncio
from groups.gmx_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["swap"], stream_name="gmx.swap"))
