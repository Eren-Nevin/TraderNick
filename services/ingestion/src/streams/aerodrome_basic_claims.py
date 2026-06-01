"""Stream worker: aerodrome_basic.claims."""
import asyncio
from groups.aero_basic_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["claims"], stream_name="aerodrome_basic.claims"))
