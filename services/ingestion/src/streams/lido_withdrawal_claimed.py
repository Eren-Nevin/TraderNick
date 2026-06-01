"""Stream worker: lido.withdrawal_claimed."""
import asyncio
from groups.lido_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["withdrawal_claimed"], stream_name="lido.withdrawal_claimed"))
