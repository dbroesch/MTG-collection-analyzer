"""
Load collection CSV and get missing cards per set. Uses Scryfall API.
"""

import json
import logging
import sys
from pathlib import Path

import pandas as pd

from scryfall import ScryfallClient, SET_NAMES_FILE, pull_set_cards

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
COLLECTIONS_DIR = BASE_DIR / "collections"
EXPENSIVE_CARD_THRESHOLD_USD = 50.0


def get_missing_cards(
    filename: str,
    output_format: str | None = None,
) -> list[pd.DataFrame] | dict[str, str]:
    """
    For each set in the collection CSV, return the cards you're missing.

    Args:
        filename: Path to collection CSV.
        output_format: If "starcity" or "card_kingdom", return a dict (set name -> string of cards)
                instead of list of DataFrames. Otherwise return list of DataFrames.

    Returns:
        List of DataFrames (default), or dict from starcity_format/cardkingdom_format.
    """
    collection_dfs = load_collection_csv(filename)
    missing_dfs = []

    for collection_df in collection_dfs:
        edition_name = collection_df["Edition Name"].iloc[0]
        collection_ids = collection_df["Scryfall ID"].dropna().astype(str)
        full_set_df = pull_set_cards(edition_name)
        missing_df = full_set_df[~full_set_df["id"].astype(str).isin(collection_ids)]
        missing_dfs.append(missing_df.reset_index(drop=True))

        logger.info(
            "%s: set=%d | in_collection=%d | missing=%d",
            edition_name, len(full_set_df), len(collection_df), len(missing_df),
        )

    if output_format and output_format.lower() == "starcity":
        return starcity_format(missing_dfs)
    if output_format and output_format.lower() == "card_kingdom":
        return cardkingdom_format(missing_dfs)
    return missing_dfs


## helper functions
def load_collection_csv(filename: str) -> list[pd.DataFrame]:
    """Load a CSV, split by edition. Returns list of DataFrames, one per set."""
    path = COLLECTIONS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    df = pd.read_csv(path)
    return [group.reset_index(drop=True) for _, group in df.groupby("Edition Name")]


def starcity_format(missing_dfs: list[pd.DataFrame]) -> dict[str, str]:
    """Convert missing card DataFrames to StarCity Games format: set name -> card lines."""
    result = {}
    for df in missing_dfs:
        if len(df) == 0:
            continue
        set_name = df["set_name"].iloc[0]
        result[set_name] = "\n".join(
            f"{name} ({code})" for name, code in zip(df["name"], df["set"])
        )
    return result


def cardkingdom_format(missing_dfs: list[pd.DataFrame]) -> dict[str, str]:
    """Convert missing card DataFrames to Card Kingdom format: set name -> card names."""
    result = {}
    for df in missing_dfs:
        if len(df) == 0:
            continue
        set_name = df["set_name"].iloc[0]
        result[set_name] = "\n".join(df["name"].tolist())
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("sets", "--sets", "-s"):
        client = ScryfallClient()
        sets_data = client.get("/sets").get("data", [])
        name_to_code = {s["name"]: s["code"] for s in sets_data}
        with open(SET_NAMES_FILE, "w", encoding="utf-8") as f:
            json.dump(name_to_code, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(name_to_code)} sets to {SET_NAMES_FILE}")
    else:
        filename = sys.argv[1] if len(sys.argv) > 1 else "collection_2_25_26.csv"
        for df in get_missing_cards(filename):
            edition = df["set_name"].iloc[0] if len(df) > 0 else "?"
            print(f"Missing from {edition}: {len(df)} cards")
            if len(df) > 0:
                print(df[["name", "collector_number", "rarity"]].head())
            print()
