"""Stream worker: binance.long_short_ratios."""
import asyncio
from groups.binance_long_short_ratios import main

if __name__ == "__main__":
    asyncio.run(main(stream_name="binance.long_short_ratios"))
