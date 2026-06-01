"""Stream worker: spark.repay."""
import asyncio
from groups.spark_events import _run

if __name__ == "__main__":
    asyncio.run(_run(events_filter=["repay"], stream_name="spark.repay"))
