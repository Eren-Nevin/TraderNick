"""Stream worker: spark.flashloan."""
import asyncio
from groups.spark_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["flashloan"], stream_name="spark.flashloan"))
