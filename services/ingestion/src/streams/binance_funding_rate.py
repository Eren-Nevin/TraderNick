"""Stream worker: binance.funding_rate."""
import asyncio
from groups.binance_funding_rate import main

if __name__ == "__main__":
    asyncio.run(main(stream_name="binance.funding_rate"))
