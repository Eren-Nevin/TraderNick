"""Stream worker: lido.withdrawal_request."""
import asyncio
from groups.lido_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["withdrawal_request"], stream_name="lido.withdrawal_request"))
