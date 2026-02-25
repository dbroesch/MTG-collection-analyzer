# MTG Collection Analyzer

Compare your Magic: The Gathering collection against full set data from the [Scryfall API](https://scryfall.com/docs/api) to see which cards you're missing from each set.

## Requirements

- Python 3.10+
- pandas

```bash
pip install pandas
```

## Setup

1. **Refresh set data** (run once, or when new sets release):

   ```bash
   python missing_cards.py sets
   ```

   This creates `set_names.json` with all set names and codes from Scryfall.

## Collection CSV Format

Place your collection export in the `collections/` directory. The CSV must include:

- **Edition Name** – Set name (e.g. "Visions", "Mirage")
- **Scryfall ID** – Card UUID from Scryfall

Example columns: `Name`, `Condition`, `Language`, `Edition Name`, `Edition Code`, `Multiverse Id`, `Scryfall ID`, `Collector Number`, `Rarity`

## Usage

### Command line

```bash
python missing_cards.py collection_2_25_26.csv
```

Or with the default file (`visions_collection_2_24_26.csv`):

```bash
python missing_cards.py
```

When run, `get_missing_cards` prints debug output for each set: set name, total cards in set, cards in your collection, and missing count. Example:

```
[DEBUG] Visions: set=167 | in_collection=37 | missing=130
[DEBUG] Mirage: set=350 | in_collection=50 | missing=300
```

### Python / Jupyter

```python
from missing_cards import get_missing_cards, load_collection_csv

# Get DataFrames of missing cards (one per set in your collection)
# Prints debug stats per set: set name, total cards, in collection, missing
missing_dfs = get_missing_cards("collection_2_25_26.csv")

for df in missing_dfs:
    print(f"Missing: {len(df)} cards")
    print(df[["name", "collector_number", "rarity"]].head())
```

### Multiple sets

If your CSV has cards from multiple sets, `get_missing_cards` returns one DataFrame per set, each with only the missing cards for that set.

## Project structure

```
.
├── missing_cards.py           # Main script
├── collection comparer.ipynb  # Jupyter notebook for analysis
├── set_names.json             # Set name → code lookup (generated)
├── collections/               # Your collection CSV files
│   ├── collection_2_25_26.csv
│   ├── visions_collection_2_24_26.csv
│   └── ...
└── README.md
```

## License

Uses card data from [Scryfall](https://scryfall.com) under their [Fan Content Policy](https://company.wizards.com/fancontentpolicy).
