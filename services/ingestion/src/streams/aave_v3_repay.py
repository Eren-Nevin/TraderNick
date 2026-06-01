"""Stream worker: aave_v3.repay."""
import asyncio
from groups.aave_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["repay"], stream_name="aave_v3.repay"))
