"""Stream worker: gmx.fees_collected."""
import asyncio
from groups.gmx_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["fees_collected"], stream_name="gmx.fees_collected"))
