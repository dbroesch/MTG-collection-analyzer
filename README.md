# MTG Collection Analyzer

A web application to manage your Magic: The Gathering collection and track missing cards using the [Scryfall API](https://scryfall.com/docs/api).

**Features:**
- **Persistent Collection Database**: Store your collection in PostgreSQL with full Scryfall data
- **Bulk Import**: Import cards from Archidekt CSV exports with automatic data enrichment
- **Manual Card Entry**: Search and add cards by name or set with owned card indicators
- **Missing Cards Analysis**: Auto-updating analysis showing missing cards per set
- **Collection Value Tracking**: See your collection value and cost to complete each set
- **Card Showcase**: Display your high-value cards ($50+) in a dedicated section
- **Grid & Table Views**: Browse your collection with sorting and pagination
- **Export Options**: Copy/download missing card lists in Card Kingdom or Star City Games format
- **High-Value Missing Cards**: Identify expensive cards you're missing ($50+)
- Supports all card variants including basic land art variations
- Beautiful MTG-inspired web interface

## Web Application

### Run Locally

```bash
cd website
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000 in your browser.

**With PostgreSQL (required for full functionality):**

1. Install PostgreSQL and create a database:
   ```bash
   brew install postgresql
   brew services start postgresql
   createdb mtg_collection
   ```

2. Set the `DATABASE_URL` environment variable:
   ```bash
   export DATABASE_URL="postgresql://localhost/mtg_collection"
   python app.py
   ```

Without `DATABASE_URL`, the app runs without database features (collection management disabled).

### Deploy to Render

This repo includes a `render.yaml` for one-click deployment to [Render](https://render.com) with PostgreSQL:

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → New → Blueprint
3. Connect your GitHub repo
4. Render auto-detects the config and deploys both the web service and PostgreSQL database

The `render.yaml` automatically:
- Creates a free PostgreSQL database
- Sets the `DATABASE_URL` environment variable
- Deploys the Flask application

**Manual setup:**
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `cd website && gunicorn app:app`
- **Environment**: Add `DATABASE_URL` from your PostgreSQL instance

---

## Using the Web App

### Building Your Collection

1. **Bulk Import** - Import cards from an [Archidekt](https://archidekt.com/) CSV export:
   - Export your collection from Archidekt as CSV
   - Go to My Collection → Bulk Import
   - Drop your CSV file to import all cards

2. **Manual Entry** - Search and add cards individually:
   - Go to My Collection → Add Cards
   - Search by card name or filter by set code
   - Cards you already own show a green "Owned" badge

### Viewing Your Collection

- **Grid View**: Visual card display with pagination (20/50/100/All per page)
- **Table View**: Sortable columns (Name, Set, Rarity, Price)
- **Filters**: Filter by set or search by card name
- **Actions**: Adjust quantities or remove cards

### Analyzing Missing Cards

The Missing Cards Analysis section automatically updates when your collection changes:
- See cards owned vs. total per set
- View collection value and cost to complete
- Export missing cards in Card Kingdom or Star City Games format
- Identify high-value missing cards ($50+)

### Card Showcase

The Card Showcase displays all cards in your collection worth $50 or more, sorted by value.

---

## Command Line Usage

### Requirements

- Python 3.10+
- pandas

```bash
pip install pandas
```

### Setup

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

The exported CSV should include **Scryfall ID** and **Quantity** columns.

## Collection CSV Format

Place your collection export in the `collections/` directory. The CSV must include:

- **Scryfall ID** – Card UUID from Scryfall
- **Quantity** – Number of copies (optional, defaults to 1)

Example columns: `Name`, `Condition`, `Language`, `Edition Name`, `Edition Code`, `Scryfall ID`, `Collector Number`, `Quantity`

## Usage

### Command line

```bash
python missing_cards.py collection_2_25_26.csv
```

Or with the default file (`collection_2_25_26.csv`):

```bash
python missing_cards.py
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

## Purchasing missing cards

Use the missing cards list to add them to a cart on your preferred deck builder:

- **Card Kingdom** – Use `format="card_kingdom"` to get card names only. Paste into the [Card Kingdom Deck Builder](https://www.cardkingdom.com/builder).
- **Star City Games** – Use `format="starcity"` to get `Card Name (set_code)` format. Paste into the [Star City Games Deck Builder](https://starcitygames.com/shop/deck-builder/).

## Project structure

```
.
├── website/                   # Flask web application
│   ├── app.py                 # Flask app (main entry point)
│   ├── requirements.txt       # Web app dependencies
│   └── templates/
│       └── index.html         # Web UI
├── missing_cards.py           # Command-line script
├── collection comparer.ipynb  # Jupyter notebook for analysis
├── set_names.json             # Set name → code lookup (generated)
├── collections/               # Your collection CSV files
│   └── ...
├── render.yaml                # Render deployment config
├── requirements.txt           # Root dependencies (for deployment)
└── README.md
```

## License

**MTG Collection Analyzer** – You may use this script for any purpose, personal or commercial. Attribution is required: credit the original author and/or this repository when you use, modify, or distribute it.

**Card data** – Uses card data from [Scryfall](https://scryfall.com) under their [Fan Content Policy](https://company.wizards.com/fancontentpolicy).
