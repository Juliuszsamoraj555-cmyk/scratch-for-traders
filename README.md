# AlgoPuzzle — MVP

A no-code visual builder that compiles drag-and-drop trading blocks into a deployable MetaTrader 5 Expert Advisor (.mq5).

## Run it locally

1. **Backend**
   ```bash
   pip install -r requirements.txt
   uvicorn main:app --reload --port 8000
   ```

2. **Frontend**
   Just open `index.html` directly in your browser (double-click it, or serve it with any static server, e.g. `python -m http.server 5500`). It talks to the backend at `http://127.0.0.1:8000`.

## How it works

- **index.html** defines custom Blockly blocks (Asset, Timeframe, Moving Average, RSI, Close Price, Comparison, Open BUY/SELL Action, and a top-level "IF Strategy Rule" wrapper). Clicking **Export to MT5** walks the block tree with a custom serializer (`serializeWorkspace`) and posts a clean JSON payload to `/api/generate` — it does not rely on Blockly's generic XML dump, so the backend gets a predictable shape.
- **main.py** validates that JSON with Pydantic, generates a complete `.mq5` file (indicator handles in `OnInit`, `CopyBuffer` reads + your condition in `OnTick`, trade execution via `CTrade`), writes a `README.txt` setup guide, zips both in memory, and streams the `.zip` back as a download.

## Notes / MVP scope

- One top-level strategy rule (`IF` block) is exported; the first one found on the canvas is used.
- Backtesting is intentionally out of scope — review the generated code and run it through MT5's Strategy Tester before trading it.
- `==` comparisons compile to a small-tolerance `MathAbs()` check, since MQL5 doubles shouldn't be compared with strict equality.
