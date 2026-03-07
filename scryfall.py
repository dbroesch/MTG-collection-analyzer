"""Shared Scryfall API client and utilities."""

import json
import logging
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
SET_NAMES_FILE = BASE_DIR / "set_names.json"

_set_name_cache: dict | None = None


class ScryfallClient:
    """Scryfall API client using stdlib urllib."""

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

    def _request(self, url: str, data: bytes | None = None, extra_headers: dict | None = None) -> dict:
        self._rate_limit()
        headers = {**self.headers, **(extra_headers or {})}
        req = Request(url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            try:
                err_data = json.loads(e.read().decode())
            except Exception:
                err_data = {}
            msg = err_data.get("details") or err_data.get("message") or str(e.reason) or "Unknown error"
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

    def search_cards(self, query: str, page: int = 1, unique: str | None = None) -> dict:
        params: dict = {"q": query, "page": page}
        if unique:
            params["unique"] = unique
        return self.get("/cards/search", params=params)

    def get_cards_collection(self, scryfall_ids: list[str]) -> list[dict]:
        """Fetch up to 75 cards by Scryfall ID in a single POST request.

        Uses Scryfall's /cards/collection endpoint instead of one GET per card,
        reducing N API calls to ceil(N/75) calls.
        """
        if not scryfall_ids:
            return []
        body = json.dumps({"identifiers": [{"id": sid} for sid in scryfall_ids]}).encode()
        result = self._request(
            f"{self.base_url}/cards/collection",
            data=body,
            extra_headers={"Content-Type": "application/json"},
        )
        return result.get("data", [])


def load_set_names() -> dict:
    """Load set name → code mapping from JSON, cached after first load."""
    global _set_name_cache
    if _set_name_cache is None:
        if not SET_NAMES_FILE.exists():
            raise FileNotFoundError(
                "set_names.json not found. Run 'python missing_cards.py sets' first."
            )
        with open(SET_NAMES_FILE, encoding="utf-8") as f:
            _set_name_cache = json.load(f)
    return _set_name_cache


def pull_set_cards(set_name: str, client: ScryfallClient | None = None) -> pd.DataFrame:
    """Fetch all cards from a set via Scryfall API. Returns DataFrame."""
    name_to_code = load_set_names()

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
    if client is None:
        client = ScryfallClient()

    all_cards = []
    data = client.search_cards(f"set:{set_code}", page=1, unique="prints")
    while True:
        all_cards.extend(data.get("data", []))
        next_page = data.get("next_page")
        if not next_page:
            break
        data = client.get_url(next_page)

    return pd.DataFrame(all_cards)
