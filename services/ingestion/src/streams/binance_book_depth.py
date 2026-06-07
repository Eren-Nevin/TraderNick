"""Stream worker: binance.book_depth."""
import asyncio
from groups.binance_book_depth import main

if __name__ == "__main__":
    asyncio.run(main(stream_name="binance.book_depth"))
