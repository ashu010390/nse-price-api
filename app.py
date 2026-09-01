from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import csv, io, time
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)
CORS(app)

# In-memory cache: symbol -> (price, fetched_at)
_cache = {}
CACHE_TTL = 300  # 5 minutes

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


def fetch_stooq(symbol):
    """Primary source — Stooq CSV, no auth required."""
    url = f'https://stooq.com/q/l/?s={symbol.lower()}&f=sd2t2ohlcv&h&e=csv'
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        close = row.get('Close', '').strip()
        if close and close != 'N/D':
            return round(float(close), 2)
    return None


def fetch_yfinance(symbol):
    """Fallback — yfinance / Yahoo Finance."""
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period='1d')
    if not hist.empty:
        return round(float(hist['Close'].iloc[-1]), 2)
    info = ticker.info
    p = info.get('regularMarketPrice') or info.get('currentPrice')
    return round(p, 2) if p else None


def _fetch_one(symbol):
    """Try stooq first, yfinance second. Return (symbol, price, cached)."""
    symbol = symbol.upper()

    if symbol in _cache:
        p, ts = _cache[symbol]
        if time.time() - ts < CACHE_TTL:
            return symbol, p, True

    price = None
    for fn in [fetch_stooq, fetch_yfinance]:
        try:
            price = fn(symbol)
            if price is not None:
                break
        except Exception:
            continue

    if price is not None:
        _cache[symbol] = (price, time.time())
    return symbol, price, False


@app.route('/')
def index():
    return jsonify({'status': 'ok', 'endpoints': [
        '/price?symbol=TITAN.NS',
        '/prices?symbols=TITAN.NS,INFY.NS,RELIANCE.NS'
    ]})


@app.route('/price')
def price():
    symbol = request.args.get('symbol', '').strip()
    if not symbol:
        return jsonify({'error': 'symbol required'}), 400
    sym, p, cached = _fetch_one(symbol)
    if p is None:
        return jsonify({'error': 'no price found'}), 404
    return jsonify({'symbol': sym, 'price': p, 'cached': cached})


@app.route('/prices')
def prices():
    raw = request.args.get('symbols', '').strip()
    symbols = [s.strip() for s in raw.split(',') if s.strip()]
    if not symbols:
        return jsonify({'error': 'symbols required'}), 400

    result = {}
    with ThreadPoolExecutor(max_workers=min(len(symbols), 10)) as ex:
        futures = {ex.submit(_fetch_one, sym): sym for sym in symbols}
        for f in as_completed(futures):
            sym, p, _ = f.result()
            result[sym] = p

    return jsonify({'prices': result})
