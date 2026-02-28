"""
Flask web app for MTG Collection Analyzer.
Upload a collection CSV and view missing cards per set.
"""

import io
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from flask import Flask, render_template, request, jsonify
import pandas as pd

app = Flask(__name__)

BASE_DIR = Path(__file__).parent.parent
SET_NAMES_FILE = BASE_DIR / "set_names.json"


class ScryfallClient:
    """Scryfall API client."""

    def __init__(self, base_url: str = "https://api.scryfall.com", delay_ms: float = 75, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.headers = {"User-Agent": "MTGSetCollector/1.0", "Accept": "application/json;q=0.9,*/*;q=0.8"}
        self.delay_sec = delay_ms / 1000.0
        self.timeout = timeout
        self._last_request_time = 0.0

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.delay_sec:
            time.sleep(self.delay_sec - elapsed)
        self._last_request_time = time.monotonic()

    def _request(self, url: str) -> dict:
        self._rate_limit()
        req = Request(url, headers=self.headers, method="GET")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            try:
                data = json.loads(e.read().decode())
            except Exception:
                data = {}
            msg = data.get("details") or data.get("message") or str(e.reason) or "Unknown error"
            raise RuntimeError(msg) from e

    def get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return self._request(url)

    def get_url(self, url: str) -> dict:
        return self._request(url)

    def get_set(self, set_code: str) -> dict:
        return self.get(f"/sets/{set_code}")

    def search_cards(self, query: str, page: int = 1) -> dict:
        return self.get("/cards/search", params={"q": query, "page": page})


def _pull_set_cards(set_name: str) -> pd.DataFrame:
    """Fetch all cards from a set via Scryfall API."""
    if not SET_NAMES_FILE.exists():
        raise FileNotFoundError("set_names.json not found. Run 'python missing_cards.py sets' first.")
    with open(SET_NAMES_FILE, encoding="utf-8") as f:
        name_to_code = json.load(f)

    set_name_clean = set_name.strip()
    set_code = name_to_code.get(set_name_clean)
    if set_code is None:
        set_name_lower = set_name_clean.lower()
        for name, code in name_to_code.items():
            if name.lower() == set_name_lower:
                set_code = code
                break
    if set_code is None:
        raise ValueError(f"Set '{set_name}' not found in set_names.json.")

    set_code = set_code.lower()
    client = ScryfallClient()
    all_cards = []
    data = client.search_cards(f"set:{set_code}", page=1)

    while True:
        all_cards.extend(data.get("data", []))
        next_page = data.get("next_page")
        if not next_page:
            break
        data = client.get_url(next_page)

    return pd.DataFrame(all_cards)


def process_uploaded_csv(file_content: bytes) -> list[pd.DataFrame]:
    """Load CSV from uploaded bytes, split by edition."""
    df = pd.read_csv(io.BytesIO(file_content))
    return [group.reset_index(drop=True) for _, group in df.groupby("Edition Name")]


def get_card_price(card: dict) -> float:
    """Extract USD price from card data. Returns 0.0 if unavailable."""
    prices = card.get("prices", {})
    usd = prices.get("usd")
    if usd:
        try:
            return float(usd)
        except (ValueError, TypeError):
            pass
    return 0.0


def calculate_collection_value(full_set_df: pd.DataFrame, collection_ids: set) -> float:
    """Calculate total USD value of cards in collection."""
    collection_df = full_set_df[full_set_df["id"].astype(str).isin(collection_ids)]
    total = 0.0
    for _, row in collection_df.iterrows():
        total += get_card_price(row.to_dict())
    return round(total, 2)


def calculate_missing_value(missing_df: pd.DataFrame) -> float:
    """Calculate total USD value of missing cards."""
    total = 0.0
    for _, row in missing_df.iterrows():
        total += get_card_price(row.to_dict())
    return round(total, 2)


def get_missing_cards_from_upload(file_content: bytes) -> list[dict]:
    """Process uploaded CSV and return missing cards info per set."""
    collection_dfs = process_uploaded_csv(file_content)
    results = []

    for collection_df in collection_dfs:
        edition_name = collection_df["Edition Name"].iloc[0]
        collection_ids = set(collection_df["Scryfall ID"].dropna().astype(str))
        
        try:
            full_set_df = _pull_set_cards(edition_name)
            missing_df = full_set_df[~full_set_df["id"].astype(str).isin(collection_ids)]
            missing_df = missing_df.reset_index(drop=True)

            set_code = missing_df["set"].iloc[0] if len(missing_df) > 0 else ""
            
            collection_value = calculate_collection_value(full_set_df, collection_ids)
            missing_value = calculate_missing_value(missing_df)
            
            card_kingdom_lines = []
            starcity_lines = []
            expensive_cards = []
            
            for _, row in missing_df.iterrows():
                price = get_card_price(row.to_dict())
                card_kingdom_lines.append(row["name"])
                starcity_lines.append(f"{row['name']} ({row['set']})")
                
                if price >= 50.0:
                    image_uris = row.get("image_uris", {})
                    if isinstance(image_uris, dict):
                        image_url = image_uris.get("normal") or image_uris.get("small") or ""
                    else:
                        image_url = ""
                    
                    expensive_cards.append({
                        "name": row["name"],
                        "price": price,
                        "collector_number": row.get("collector_number", ""),
                        "rarity": row.get("rarity", ""),
                        "image_url": image_url,
                        "scryfall_url": row.get("scryfall_uri", ""),
                    })
            
            expensive_cards.sort(key=lambda x: x["price"], reverse=True)

            results.append({
                "set_name": edition_name,
                "set_code": set_code,
                "total_in_set": len(full_set_df),
                "in_collection": len(collection_df),
                "missing_count": len(missing_df),
                "collection_value": collection_value,
                "missing_value": missing_value,
                "card_kingdom": "\n".join(card_kingdom_lines),
                "starcity": "\n".join(starcity_lines),
                "expensive_cards": expensive_cards,
            })
        except Exception as e:
            results.append({
                "set_name": edition_name,
                "set_code": "",
                "total_in_set": 0,
                "in_collection": len(collection_df),
                "missing_count": 0,
                "collection_value": 0.0,
                "missing_value": 0.0,
                "card_kingdom": "",
                "starcity": "",
                "error": str(e),
            })

    return results


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    
    try:
        content = file.read()
        results = get_missing_cards_from_upload(content)
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    import os
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=debug)
