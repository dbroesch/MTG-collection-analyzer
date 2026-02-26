# MTG Collection Analyzer

Compare your Magic: The Gathering collection against full set data from the [Scryfall API](https://scryfall.com/docs/api) to see which cards you're missing from each set. Outputs a Dataframe in either Starcity Games deck builder format or Card Kingdom format for easy purchasing!

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

## Getting your collection file

You can export your collection as a CSV from [Archidekt](https://archidekt.com/):

1. Log in to Archidekt and go to your **Collection**
2. Use the export option to download your collection as CSV
3. Save the file to the `collections/` directory

The exported CSV should include **Edition Name** and **Scryfall ID** columns for the analyzer to work.

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

Or with the default file (`collection_2_25_26.csv`):

```bash
python missing_cards.py
```

When run, `get_missing_cards` prints debug output for each set: set name, total cards in set, cards in your collection, and missing count. Example:

```
[DEBUG] Visions: set=167 | in_collection=37 | missing=130
[DEBUG] Mirage: set=350 | in_collection=50 | missing=300
```

### Python / Jupyter

`get_missing_cards` takes an optional second argument (`format`). When omitted or `None`, it returns a list of DataFrames (raw card data per set). When set to `"starcity"` or `"card_kingdom"`, it returns a dictionary formatted for pasting into those deck builders.

```python
from missing_cards import get_missing_cards, load_collection_csv

# Default: list of DataFrames (one per set)
missing_dfs = get_missing_cards("collection_2_25_26.csv")
for df in missing_dfs:
    print(f"Missing: {len(df)} cards")
    print(df[["name", "collector_number", "rarity"]].head())

# Formatted for Star City Games deck builder: set name -> "Card Name (set_code)"
starcity_format = get_missing_cards("collection_2_25_26.csv", format="starcity")

# Formatted for Card Kingdom deck builder: set name -> card names only
ck_format = get_missing_cards("collection_2_25_26.csv", format="card_kingdom")
```

### Multiple sets

If your CSV has cards from multiple sets, `get_missing_cards` returns one DataFrame per set, each with only the missing cards for that set.

## Purchasing missing cards

Use the missing cards list from the script to add them to a cart on your preferred deck builder:

- **Card Kingdom** – Use `format="card_kingdom"` to get card names only. Paste into the [Card Kingdom Deck Builder](https://www.cardkingdom.com/builder).
- **Star City Games** – Use `format="starcity"` to get `Card Name (set_code)` format. Paste into the [Star City Games Deck Builder](https://starcitygames.com/shop/deck-builder/).

1. Run `get_missing_cards("your_collection.csv", format="card_kingdom")` or `format="starcity"`.
2. Copy the string for the set you want (e.g. `result["Visions"]`).
3. Paste into the deck builder to build a list and purchase.

## Project structure

```
.
├── missing_cards.py           # Main script
├── collection comparer.ipynb  # Jupyter notebook for analysis
├── set_names.json             # Set name → code lookup (generated)
├── collections/               # Your collection CSV files
│   ├── collection_2_25_26.csv
│   └── ...
└── README.md
```

## License

Uses card data from [Scryfall](https://scryfall.com) under their [Fan Content Policy](https://company.wizards.com/fancontentpolicy).
