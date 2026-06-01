"""Stream worker: gmx.withdraw."""
import asyncio
from groups.gmx_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["withdraw"], stream_name="gmx.withdraw"))
