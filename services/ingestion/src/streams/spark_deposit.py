"""Stream worker: spark.deposit."""
import asyncio
from groups.spark_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["deposit"], stream_name="spark.deposit"))
