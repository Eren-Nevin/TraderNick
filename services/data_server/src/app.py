from sanic import Sanic, response

from routes.aave import bp as aave_bp
from routes.derivatives import bp as derivatives_bp
from routes.groups import bp as groups_bp
from routes.ohlcv import bp as ohlcv_bp
from routes.trade_volume import bp as trade_volume_bp
from routes.transfers import bp as transfers_bp
from routes.transfers_streams import bp as transfers_streams_bp
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

app = Sanic("tradernick_data_server")
app.config.RESPONSE_TIMEOUT = 60

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


@app.get("/health")
async def health(_request):
    return response.json({"ok": True})
