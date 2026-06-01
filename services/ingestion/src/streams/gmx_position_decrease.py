"""Stream worker: gmx.position_decrease."""
import asyncio
from groups.gmx_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["position_decrease"], stream_name="gmx.position_decrease"))
