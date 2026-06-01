"""Stream worker: aerodrome.withdrawals."""
import asyncio
from groups.aero_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["withdrawals"], stream_name="aerodrome.withdrawals"))
