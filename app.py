from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import csv, io, time
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)
CORS(app)

# In-memory cache: symbol -> (price, prev_close, fetched_at)
_cache = {}
CACHE_TTL = 300  # 5 minutes

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


def fetch_stooq(symbol):
    """Primary source — Stooq daily CSV. Returns (close, prev_close).
    Fetches last 2 trading days; prev_close is the second-to-last row's Close."""
    url = f'https://stooq.com/q/d/l/?s={symbol.lower()}&i=d'
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    rows = []
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        close = row.get('Close', '').strip()
        if close and close != 'N/D':
            rows.append(round(float(close), 2))
    if not rows:
        return None, None
    close_val = rows[-1]
    prev_close = rows[-2] if len(rows) >= 2 else None
    return close_val, prev_close


def fetch_yfinance(symbol):
    """Fallback — yfinance. Returns (close, prev_close)."""
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period='5d')
    if len(hist) >= 2:
        close = round(float(hist['Close'].iloc[-1]), 2)
        prev_close = round(float(hist['Close'].iloc[-2]), 2)
        return close, prev_close
    if len(hist) == 1:
        close = round(float(hist['Close'].iloc[-1]), 2)
        return close, None
    info = ticker.info
    p = info.get('regularMarketPrice') or info.get('currentPrice')
    pc = info.get('previousClose') or info.get('regularMarketPreviousClose')
    return (round(p, 2), round(pc, 2) if pc else None) if p else (None, None)


def _fetch_one(symbol):
    """Try stooq first, yfinance second. Return (symbol, price, prev_close, cached)."""
    symbol = symbol.upper()

    if symbol in _cache:
        p, pc, ts = _cache[symbol]
        if time.time() - ts < CACHE_TTL:
            return symbol, p, pc, True

    price, prev_close = None, None
    for fn in [fetch_stooq, fetch_yfinance]:
        try:
            price, prev_close = fn(symbol)
            if price is not None:
                break
        except Exception:
            continue

    if price is not None:
        _cache[symbol] = (price, prev_close, time.time())
    return symbol, price, prev_close, False


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
    sym, p, pc, cached = _fetch_one(symbol)
    if p is None:
        return jsonify({'error': 'no price found'}), 404
    return jsonify({'symbol': sym, 'price': p, 'prev_close': pc, 'cached': cached})


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
            sym, p, pc, _ = f.result()
            result[sym] = {'price': p, 'prev_close': pc}

    return jsonify({'prices': result})
