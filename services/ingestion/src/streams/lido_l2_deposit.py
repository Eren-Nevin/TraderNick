"""Stream worker: lido.l2_deposit."""
import asyncio
from groups.lido_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["l2_deposit"], stream_name="lido.l2_deposit"))
