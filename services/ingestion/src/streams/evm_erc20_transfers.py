"""Stream worker: transfers.evm_erc20."""
import asyncio
from groups.evm_erc20_transfers import main

if __name__ == "__main__":
    asyncio.run(main(stream_name="transfers.evm_erc20"))
