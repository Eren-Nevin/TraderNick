"""Stream worker: gmx.liquidation."""
import asyncio
from groups.gmx_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["liquidation"], stream_name="gmx.liquidation"))
