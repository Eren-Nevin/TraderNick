"""Stream worker: aerodrome.collects."""
import asyncio
from groups.aero_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["collects"], stream_name="aerodrome.collects"))
