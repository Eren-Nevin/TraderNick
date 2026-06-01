"""Stream worker: morpho.supply_collateral."""
import asyncio
from groups.morpho_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["supply_collateral"], stream_name="morpho.supply_collateral"))
