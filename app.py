from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)
CORS(app)

# In-memory cache: symbol -> (price, fetched_at)
_cache = {}
CACHE_TTL = 300  # 5 minutes

def _fetch_one(symbol):
    """Fetch price for one symbol, using cache when fresh."""
    if symbol in _cache:
        p, ts = _cache[symbol]
        if time.time() - ts < CACHE_TTL:
            return symbol, p, True   # (sym, price, cached)
    try:
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period='1d')
        if not hist.empty:
            p = float(hist['Close'].iloc[-1])
        else:
            info = ticker.info
            p = info.get('regularMarketPrice') or info.get('currentPrice')
        if p is None:
            return symbol, None, False
        p = round(p, 2)
        _cache[symbol] = (p, time.time())
        return symbol, p, False
    except Exception:
        return symbol, None, False


@app.route('/')
def index():
    return jsonify({'status': 'ok', 'endpoints': [
        '/price?symbol=TITAN.NS',
        '/prices?symbols=TITAN.NS,INFY.NS,RELIANCE.NS'
    ]})


@app.route('/price')
def price():
    """Single symbol."""
    symbol = request.args.get('symbol', '').strip().upper()
    if not symbol:
        return jsonify({'error': 'symbol required'}), 400
    sym, p, cached = _fetch_one(symbol)
    if p is None:
        return jsonify({'error': 'no price found'}), 404
    return jsonify({'symbol': sym, 'price': p, 'cached': cached})


@app.route('/prices')
def prices():
    """Batch — comma-separated symbols, fetched in parallel (max 10)."""
    raw     = request.args.get('symbols', '').strip()
    symbols = [s.strip().upper() for s in raw.split(',') if s.strip()]
    if not symbols:
        return jsonify({'error': 'symbols required'}), 400

    result = {}
    with ThreadPoolExecutor(max_workers=min(len(symbols), 10)) as ex:
        futures = {ex.submit(_fetch_one, sym): sym for sym in symbols}
        for f in as_completed(futures):
            sym, p, _ = f.result()
            result[sym] = p   # None means fetch failed for that symbol

    return jsonify({'prices': result})
