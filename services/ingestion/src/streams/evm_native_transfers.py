"""Stream worker: transfers.evm_native."""
import asyncio
from groups.evm_native_transfers import main

if __name__ == "__main__":
    asyncio.run(main(stream_name="transfers.evm_native"))
