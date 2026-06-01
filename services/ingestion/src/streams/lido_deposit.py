"""Stream worker: lido.deposit."""
import asyncio
from groups.lido_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["deposit"], stream_name="lido.deposit"))
