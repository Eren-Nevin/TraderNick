# tradernick-data-provider

Python client for the TraderNick `data_provider` service. Drop-in
compatible with [`horatio-data-provider`](https://pypi.org/project/horatio-data-provider/):
same `DataProviderClient` class, same namespaces (`evm`, `tron`, `btc`,
`binance`, `hyperliquid`, `wallets`, `cache`, `jobs`), same chainable
builder methods, same `as_pandas()` / `as_polars()` / `as_parquet()`
terminators. The only visible difference is the import path.

```python
# Before
from horatio_data_provider import DataProviderClient

# After
from tradernick_data_provider import DataProviderClient
```

The server URL passed to the constructor is the only thing you need to
change at the call site.

## Status

**0.1.0 — Phase 0 scaffold.** Source is a verbatim fork of the
horatio-data-provider client. No behavior changes yet; the server-side
data_provider service handles the swap from DeFiStream+parquet to
ClickHouse transparently.
