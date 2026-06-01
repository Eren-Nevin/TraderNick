"""Stream worker: spark.borrow."""
import asyncio
from groups.spark_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["borrow"], stream_name="spark.borrow"))
