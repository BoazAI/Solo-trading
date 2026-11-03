#!/usr/bin/env python3
"""Solo-trading: papierhandel op Bitvavo met live openbare API-prijzen.
Regels: market koop = ask, market verkoop = bid, fee 0,25%.
Limit = alleen gevuld als een latere 1m-candle er strikt doorheen gaat, fee 0,15%.
Elke trade wordt direct gecommit en gepusht. Geen sleutels in deze repo."""
import csv, json, os, sys, subprocess, urllib.request, datetime as dt

API = "https://api.bitvavo.com/v2"
ROOT = os.path.dirname(os.path.abspath(__file__))
TRADES = os.path.join(ROOT, "trades.csv")
ORDERS = os.path.join(ROOT, "orders.csv")
START = 250.0
MFEE, LFEE = 0.0025, 0.0015
TF = ["tijd_utc", "paar", "actie", "aantal", "prijs", "bedrag_eur", "fee_eur", "type", "notitie"]
OF = ["id", "geplaatst_utc", "paar", "actie", "aantal", "limietprijs", "status", "afgehandeld_utc"]

def get(path):
    with urllib.request.urlopen(API + path, timeout=20) as r:
        return json.load(r)

def now():
    return dt.datetime.now(dt.timezone.utc)

def fmt(t):
    return t.strftime("%Y-%m-%d %H:%M")

def rows(p, fields):
    if not os.path.exists(p):
        with open(p, "w", newline="") as f:
            csv.writer(f).writerow(fields)
    with open(p) as f:
        return list(csv.DictReader(f))

def append(p, fields, row):
    rows(p, fields)
    with open(p, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=fields).writerow(row)

def rewrite_orders(orders):
    with open(ORDERS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OF); w.writeheader(); w.writerows(orders)

def git(msg):
    env = dict(os.environ)
    helper = '!f(){ echo username=x-access-token; echo "password=$SOLO_GITHUB_TOKEN"; }; f'
    subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
    subprocess.run(["git", "-C", ROOT, "-c", "user.name=Solo", "-c", "user.email=solo@users.noreply.github.com",
                    "commit", "-q", "-m", msg], check=True)
    for _ in range(3):
        r = subprocess.run(["git", "-C", ROOT, "-c", f"credential.helper={helper}", "push", "-q", "origin", "main"], env=env)
        if r.returncode == 0:
            print("gecommit en gepusht:", msg); return
    print("LET OP: push mislukt, commit staat lokaal:", msg)

def book(pair):
    b = get(f"/ticker/book?market={pair}")
    if not b.get("bid") or not b.get("ask"):
        raise SystemExit(f"Geen geldige prijs voor {pair}: geen trade.")
    return float(b["bid"]), float(b["ask"])

def state():
    cash, pos = START, {}
    for t in rows(TRADES, TF):
        q, e, fee = float(t["aantal"]), float(t["bedrag_eur"]), float(t["fee_eur"])
        if t["actie"] == "kopen":
            cash -= e + fee; pos[t["paar"]] = pos.get(t["paar"], 0) + q
        else:
            cash += e - fee; pos[t["paar"]] = pos.get(t["paar"], 0) - q
    pos = {k: v for k, v in pos.items() if v > 1e-12}
    reserved = sum(float(o["aantal"]) * float(o["limietprijs"]) * (1 + LFEE)
                   for o in rows(ORDERS, OF) if o["status"] == "open" and o["actie"] == "kopen")
    return cash, pos, reserved

def record(pair, side, qty, price, kind, note, t=None):
    notional = qty * price
    fee = notional * (MFEE if kind == "market" else LFEE)
    row = dict(tijd_utc=fmt(t or now()), paar=pair, actie=side, aantal=f"{qty:.8f}", prijs=f"{price:.10g}",
               bedrag_eur=f"{notional:.2f}" if False else f"{notional:.6f}", fee_eur=f"{fee:.6f}", type=kind, notitie=note)
    append(TRADES, TF, row)
    return row

def cmd_buy(pair, eur, note=""):
    cash, _, res = state(); eur = float(eur)
    if eur > cash - res + 1e-9: raise SystemExit(f"Onvoldoende vrij saldo ({cash-res:.2f}).")
    bid, ask = book(pair)
    qty = eur / (ask * (1 + MFEE))
    r = record(pair, "kopen", qty, ask, "market", note)
    git(f"trade: kopen {pair} {qty:.8f} @ {ask} (market)"); print(r)

def cmd_sell(pair, qty="all", note=""):
    _, pos, _ = state(); have = pos.get(pair, 0)
    locked = sum(float(o["aantal"]) for o in rows(ORDERS, OF) if o["status"]=="open" and o["actie"]=="verkopen" and o["paar"]==pair)
    q = have - locked if qty == "all" else float(qty)
    if q <= 0 or q > have - locked + 1e-12: raise SystemExit(f"Positie te klein ({have}, vastgezet {locked}).")
    bid, ask = book(pair)
    r = record(pair, "verkopen", q, bid, "market", note)
    git(f"trade: verkopen {pair} {q:.8f} @ {bid} (market)"); print(r)

def cmd_limit(side, pair, price, amount):
    price = float(price); cash, pos, res = state()
    if side == "kopen":
        qty = float(amount) / (price * (1 + LFEE))
        if float(amount) > cash - res + 1e-9: raise SystemExit("Onvoldoende vrij saldo.")
    else:
        qty = pos.get(pair, 0) if amount == "all" else float(amount)
        if qty <= 0 or qty > pos.get(pair, 0) + 1e-12: raise SystemExit("Positie te klein.")
    book(pair)  # markt moet bestaan en prijs bekend zijn
    orders = rows(ORDERS, OF)
    oid = str(len(orders) + 1)
    append(ORDERS, OF, dict(id=oid, geplaatst_utc=fmt(now()), paar=pair, actie=side, aantal=f"{qty:.8f}",
                            limietprijs=f"{price:.10g}", status="open", afgehandeld_utc=""))
    git(f"order {oid}: limit {side} {pair} {qty:.8f} @ {price}")

def cmd_cancel(oid):
    orders = rows(ORDERS, OF)
    for o in orders:
        if o["id"] == oid and o["status"] == "open":
            o["status"] = "geannuleerd"; o["afgehandeld_utc"] = fmt(now())
    rewrite_orders(orders); git(f"order {oid}: geannuleerd")

def cmd_check():
    orders = rows(ORDERS, OF); changed = []
    for o in orders:
        if o["status"] != "open": continue
        placed = dt.datetime.strptime(o["geplaatst_utc"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
        start = int((placed + dt.timedelta(minutes=1)).timestamp() * 1000)  # pas candles NA de plaatsingsminuut
        cs = get(f"/{o['paar']}/candles?interval=1m&start={start}&limit=1440")
        cs = sorted(cs, key=lambda c: c[0]); p = float(o["limietprijs"])
        for c in cs:
            ts, hi, lo = c[0], float(c[2]), float(c[3])
            if ts < start: continue
            if (o["actie"] == "kopen" and lo < p) or (o["actie"] == "verkopen" and hi > p):
                t = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc)
                record(o["paar"], o["actie"], float(o["aantal"]), p, "limit", f"order {o['id']}, candle {fmt(t)}", t)
                o["status"] = "gevuld"; o["afgehandeld_utc"] = fmt(t); changed.append(o["id"]); break
    if changed:
        rewrite_orders(orders); git("limit gevuld: order " + ",".join(changed))
    print("gevuld:", changed or "geen")

def cmd_status():
    cash, pos, res = state(); total = cash; out = []
    for p, q in pos.items():
        bid, _ = book(p); v = q * bid * (1 - MFEE); total += v
        out.append(dict(paar=p, aantal=q, bid=bid, waarde_netto=round(v, 2)))
    print(json.dumps(dict(cash=round(cash, 2), gereserveerd=round(res, 2), posities=out,
                          totaal_netto=round(total, 2), open_orders=[o for o in rows(ORDERS, OF) if o["status"]=="open"]), indent=1))

def cmd_scan(minvol="200000"):
    tick = get("/ticker/24h"); res = []
    for t in tick:
        m = t.get("market", "")
        if not m.endswith("-EUR") or not t.get("last") or not t.get("open"): continue
        try:
            vq = float(t.get("volumeQuote") or 0); o, l = float(t["open"]), float(t["last"])
            bid, ask = float(t.get("bid") or 0), float(t.get("ask") or 0)
        except ValueError: continue
        if vq < float(minvol) or o <= 0 or bid <= 0: continue
        res.append(dict(m=m, chg=round((l/o-1)*100, 2), vol=int(vq), spread=round((ask/bid-1)*100, 3),
                        hi=t.get("high"), lo=t.get("low"), last=l))
    res.sort(key=lambda r: -r["chg"])
    for r in res: print(json.dumps(r))

if __name__ == "__main__":
    a = sys.argv[1:]
    {"buy": cmd_buy, "sell": cmd_sell, "limit": cmd_limit, "cancel": cmd_cancel, "check": cmd_check,
     "status": cmd_status, "scan": cmd_scan, "book": lambda p: print(book(p))}[a[0]](*a[1:])
