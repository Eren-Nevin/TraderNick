"""Stream worker: morpho.withdraw_collateral."""
import asyncio
from groups.morpho_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["withdraw_collateral"], stream_name="morpho.withdraw_collateral"))
