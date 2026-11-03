# Solo-trading

Papierhandel-experiment van Solo: van €250 naar minimaal €500 (doel €1.000) op Bitvavo, 3 t/m 18 november 2026 23:59 (Amsterdam).
Alleen EUR-spotparen, alleen long, geen hefboom.

## Regels
- Prijzen komen alleen live uit de openbare Bitvavo-API.
- Market: kopen tegen ask, verkopen tegen bid, plus 0,25% fee.
- Limit: alleen gevuld als een latere 1-minuut-candle strikt door de limietprijs gaat, plus 0,15% fee.
- Elke trade staat in `trades.csv` (tijd in UTC op de minuut) en wordt direct gecommit. Commits worden nooit herschreven.
- Limietorders staan in `orders.csv` vanaf het moment van plaatsen.
- Dagelijks om 21:00 (Amsterdam) een rapport in `rapporten/<datum>.md`.

`solo.py` is het script dat prijzen ophaalt, fills berekent en commit.
