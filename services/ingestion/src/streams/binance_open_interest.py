"""Stream worker: binance.open_interest."""
import asyncio
from groups.binance_open_interest import main

if __name__ == "__main__":
    asyncio.run(main(stream_name="binance.open_interest"))
