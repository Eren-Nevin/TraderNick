"""Stream worker: data_process.exchange_flow_self_heal."""
import asyncio
from groups.data_process_exchange_flow import main

if __name__ == "__main__":
    asyncio.run(main(stream_name="data_process.exchange_flow_self_heal"))
