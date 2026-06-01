"""Stream worker: aave_v3.flashloan."""
import asyncio
from groups.aave_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["flashloan"], stream_name="aave_v3.flashloan"))
