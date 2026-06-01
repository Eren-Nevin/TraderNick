"""Stream worker: transfers.tron_trc20."""
import asyncio
from groups.tron_trc20_transfers import main

if __name__ == "__main__":
    asyncio.run(main(stream_name="transfers.tron_trc20"))
