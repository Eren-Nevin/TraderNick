from sanic import Sanic, response

from routes.aave import bp as aave_bp
from routes.derivatives import bp as derivatives_bp
from routes.groups import bp as groups_bp
from routes.ohlcv import bp as ohlcv_bp
from routes.trade_volume import bp as trade_volume_bp
from routes.transfers import bp as transfers_bp
from routes.transfers_streams import bp as transfers_streams_bp, warm_streams_cache
from routes.uniswap import bp as uniswap_bp
from routes.lido import bp as lido_bp
from routes.aave_v2 import bp as aave_v2_bp
from routes.uniswap_v2 import bp as uniswap_v2_bp
from routes.uniswap_v4 import bp as uniswap_v4_bp
from routes.aero import bp as aero_bp
from routes.aero_basic import bp as aero_basic_bp
from routes.aave_v4 import bp as aave_v4_bp
from routes.morpho import bp as morpho_bp
from routes.spark import bp as spark_bp
from routes.gmx import bp as gmx_bp
from routes.hyperliquid import bp as hyperliquid_bp
from routes.exchange_flow import bp as exchange_flow_bp
from routes.book_depth import bp as book_depth_bp
from routes.wallet_pins import bp as wallet_pins_bp, ensure_tables as ensure_wallet_pins
from throttle import register_health_endpoint
from clickhouse import client
from wallets.cache import ensure_table as ensure_wallets_cache

app = Sanic("tradernick_data_server")
# Above the frontend queuedFetch timeout (180s) and below the CH client timeout
# (300s) so a slow cold smart-wallet selection completes without any layer
# aborting it mid-flight (an abort left the set uncached → retry storm).
app.config.RESPONSE_TIMEOUT = 240
register_health_endpoint(app)

app.blueprint(ohlcv_bp)
app.blueprint(trade_volume_bp)
app.blueprint(derivatives_bp)
app.blueprint(transfers_bp)
app.blueprint(transfers_streams_bp)
app.blueprint(groups_bp)
app.blueprint(aave_bp)
app.blueprint(uniswap_bp)
app.blueprint(lido_bp)
app.blueprint(aave_v2_bp)
app.blueprint(uniswap_v2_bp)
app.blueprint(uniswap_v4_bp)
app.blueprint(aero_bp)
app.blueprint(aero_basic_bp)
app.blueprint(aave_v4_bp)
app.blueprint(morpho_bp)
app.blueprint(spark_bp)
app.blueprint(gmx_bp)
app.blueprint(hyperliquid_bp)
app.blueprint(exchange_flow_bp)
app.blueprint(book_depth_bp)
app.blueprint(wallet_pins_bp)


@app.listener("before_server_start")
async def _warm_caches(_app):
    # Prime the transfers/streams catalogue cache before serving traffic
    # so the first dashboard page-load skips the ~30s DISTINCT scan.
    await warm_streams_cache()
    # Create the smart-wallet leaderboard cache table if absent.
    await ensure_wallets_cache(await client())
    # Create the wallet pins + groups tables if absent.
    await ensure_wallet_pins(await client())


@app.get("/health")
async def health(_request):
    return response.json({"ok": True})
