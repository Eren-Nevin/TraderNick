"""Stream worker: transfers.tron_native."""
import asyncio
from groups.tron_native_transfers import main

if __name__ == "__main__":
    asyncio.run(main(stream_name="transfers.tron_native"))
