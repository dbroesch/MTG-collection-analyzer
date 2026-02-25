"""
Load collection CSV and get missing cards per set. Uses Scryfall API.
"""

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pandas as pd

BASE_DIR = Path(__file__).parent
COLLECTIONS_DIR = BASE_DIR / "collections"
SET_NAMES_FILE = BASE_DIR / "set_names.json"


class ScryfallClient:
    """Scryfall API client. Uses stdlib urllib."""

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
    """Fetch all cards from a set via Scryfall API. Returns DataFrame."""
    if not SET_NAMES_FILE.exists():
        raise FileNotFoundError(f"set_names.json not found. Run 'python missing_cards.py sets' first.")
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
    set_info = client.get_set(set_code)
    all_cards = []
    data = client.search_cards(f"set:{set_code}", page=1)

    while True:
        all_cards.extend(data.get("data", []))
        next_page = data.get("next_page")
        if not next_page:
            break
        data = client.get_url(next_page)

    return pd.DataFrame(all_cards)


def load_collection_csv(filename: str) -> list[pd.DataFrame]:
    """Load a CSV, split by edition. Returns list of DataFrames, one per set."""
    path = COLLECTIONS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    df = pd.read_csv(path)
    return [group.reset_index(drop=True) for _, group in df.groupby("Edition Name")]


def get_missing_cards(filename: str) -> list[pd.DataFrame]:
    """
    For each set in the collection CSV, return the cards you're missing.

    Returns a list of DataFrames, one per set, each containing full card data
    for the cards you don't have from that set.
    """
    collection_dfs = load_collection_csv(filename)
    missing_dfs = []

    for collection_df in collection_dfs:
        edition_name = collection_df["Edition Name"].iloc[0]
        collection_ids = collection_df["Scryfall ID"].dropna().astype(str)
        full_set_df = _pull_set_cards(edition_name)
        missing_df = full_set_df[~full_set_df["id"].astype(str).isin(collection_ids)]
        missing_dfs.append(missing_df.reset_index(drop=True))

        # Debug output
        set_size = len(full_set_df)
        in_collection = len(collection_df)
        missing_count = len(missing_df)
        print(f"[DEBUG] {edition_name}: set={set_size} | in_collection={in_collection} | missing={missing_count}")

    return missing_dfs


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("sets", "--sets", "-s"):
        client = ScryfallClient()
        sets_data = client.get("/sets").get("data", [])
        name_to_code = {s["name"]: s["code"] for s in sets_data}
        with open(SET_NAMES_FILE, "w", encoding="utf-8") as f:
            json.dump(name_to_code, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(name_to_code)} sets to {SET_NAMES_FILE}")
    else:
        filename = sys.argv[1] if len(sys.argv) > 1 else "visions_collection_2_24_26.csv"
        for df in get_missing_cards(filename):
            edition = df["set_name"].iloc[0] if len(df) > 0 else "?"
            print(f"Missing from {edition}: {len(df)} cards")
            if len(df) > 0:
                print(df[["name", "collector_number", "rarity"]].head())
            print()
