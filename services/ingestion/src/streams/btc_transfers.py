"""Stream worker: transfers.btc."""
import asyncio
from groups.btc_transfers import main

if __name__ == "__main__":
    asyncio.run(main(stream_name="transfers.btc"))
