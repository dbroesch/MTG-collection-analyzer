"""
Flask web app for MTG Collection Analyzer.
Upload a collection CSV and view missing cards per set.
"""

import io
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from flask import Flask, render_template, request, jsonify
import pandas as pd
from sqlalchemy import create_engine, Column, String, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool

app = Flask(__name__)

BASE_DIR = Path(__file__).parent.parent
SET_NAMES_FILE = BASE_DIR / "set_names.json"

# Database configuration
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

Base = declarative_base()


class CollectionCard(Base):
    """Model for cards in the collection."""
    __tablename__ = "collection"
    
    id = Column(String(36), primary_key=True)
    name = Column(Text, nullable=False, index=True)
    set_code = Column(String(10), nullable=False, index=True)
    set_name = Column(Text, nullable=False)
    collector_number = Column(String(20), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    scryfall_data = Column(JSONB, nullable=False)
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "set_code": self.set_code,
            "set_name": self.set_name,
            "collector_number": self.collector_number,
            "quantity": self.quantity,
            "scryfall_data": self.scryfall_data,
        }


engine = None
Session = None


def init_db():
    """Initialize database connection and create tables."""
    global engine, Session
    
    if not DATABASE_URL:
        print("WARNING: DATABASE_URL not set. Database features disabled.")
        return False
    
    try:
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        print("Database initialized successfully.")
        return True
    except Exception as e:
        print(f"Database initialization failed: {e}")
        return False


def get_db_session():
    """Get a new database session."""
    if Session is None:
        return None
    return Session()


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

    def search_cards(self, query: str, page: int = 1, unique: str | None = None) -> dict:
        params = {"q": query, "page": page}
        if unique:
            params["unique"] = unique
        return self.get("/cards/search", params=params)


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
    data = client.search_cards(f"set:{set_code}", page=1, unique="prints")

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
                    row_dict = row.to_dict()
                    image_uris = row_dict.get("image_uris")
                    image_url = ""
                    if isinstance(image_uris, dict):
                        image_url = image_uris.get("normal") or image_uris.get("small") or ""
                    
                    scryfall_uri = row_dict.get("scryfall_uri", "")
                    if pd.isna(scryfall_uri):
                        scryfall_uri = ""
                    
                    expensive_cards.append({
                        "name": row["name"],
                        "price": price,
                        "collector_number": row_dict.get("collector_number", ""),
                        "rarity": row_dict.get("rarity", ""),
                        "image_url": image_url,
                        "scryfall_url": scryfall_uri,
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


@app.route("/set/<set_code>", methods=["GET"])
def get_set_cards(set_code):
    """Fetch all cards from a set by set code."""
    try:
        client = ScryfallClient()
        set_code = set_code.lower().strip()
        
        set_info = client.get_set(set_code)
        set_name = set_info.get("name", set_code.upper())
        
        all_cards = []
        data = client.search_cards(f"set:{set_code}", page=1)
        
        while True:
            all_cards.extend(data.get("data", []))
            next_page = data.get("next_page")
            if not next_page:
                break
            data = client.get_url(next_page)
        
        card_lines = [f"{card['name']} ({set_code})" for card in all_cards]
        
        return jsonify({
            "set_name": set_name,
            "set_code": set_code,
            "card_count": len(all_cards),
            "cards": "\n".join(card_lines),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# Collection API endpoints

@app.route("/api/collection", methods=["GET"])
def get_collection():
    """Get all cards in the collection."""
    session = get_db_session()
    if session is None:
        return jsonify({"error": "Database not configured"}), 503
    
    try:
        set_filter = request.args.get("set")
        search = request.args.get("search", "").strip()
        
        query = session.query(CollectionCard)
        
        if set_filter:
            query = query.filter(CollectionCard.set_code == set_filter.lower())
        if search:
            query = query.filter(CollectionCard.name.ilike(f"%{search}%"))
        
        cards = query.order_by(CollectionCard.set_code, CollectionCard.name).all()
        
        return jsonify({
            "cards": [card.to_dict() for card in cards],
            "total": len(cards),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/collection", methods=["POST"])
def add_to_collection():
    """Add a card to the collection."""
    session = get_db_session()
    if session is None:
        return jsonify({"error": "Database not configured"}), 503
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        scryfall_id = data.get("id")
        quantity = data.get("quantity", 1)
        scryfall_data = data.get("scryfall_data") or data
        
        if not scryfall_id:
            return jsonify({"error": "Card ID required"}), 400
        
        existing = session.query(CollectionCard).filter_by(id=scryfall_id).first()
        
        if existing:
            existing.quantity += quantity
            existing.scryfall_data = scryfall_data
            message = "Card quantity updated"
        else:
            card = CollectionCard(
                id=scryfall_id,
                name=scryfall_data.get("name", "Unknown"),
                set_code=scryfall_data.get("set", ""),
                set_name=scryfall_data.get("set_name", ""),
                collector_number=scryfall_data.get("collector_number", ""),
                quantity=quantity,
                scryfall_data=scryfall_data,
            )
            session.add(card)
            message = "Card added to collection"
        
        session.commit()
        return jsonify({"success": True, "message": message})
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/collection/<card_id>", methods=["PUT"])
def update_collection_card(card_id):
    """Update a card's quantity in the collection."""
    session = get_db_session()
    if session is None:
        return jsonify({"error": "Database not configured"}), 503
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        card = session.query(CollectionCard).filter_by(id=card_id).first()
        if not card:
            return jsonify({"error": "Card not found"}), 404
        
        if "quantity" in data:
            card.quantity = data["quantity"]
        
        session.commit()
        return jsonify({"success": True, "card": card.to_dict()})
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/collection/<card_id>", methods=["DELETE"])
def remove_from_collection(card_id):
    """Remove a card from the collection."""
    session = get_db_session()
    if session is None:
        return jsonify({"error": "Database not configured"}), 503
    
    try:
        card = session.query(CollectionCard).filter_by(id=card_id).first()
        if not card:
            return jsonify({"error": "Card not found"}), 404
        
        session.delete(card)
        session.commit()
        return jsonify({"success": True, "message": "Card removed"})
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/collection/sets", methods=["GET"])
def get_collection_sets():
    """Get list of sets in the collection."""
    session = get_db_session()
    if session is None:
        return jsonify({"error": "Database not configured"}), 503
    
    try:
        from sqlalchemy import func, distinct
        
        results = session.query(
            CollectionCard.set_code,
            CollectionCard.set_name,
            func.count(CollectionCard.id).label("card_count"),
            func.sum(CollectionCard.quantity).label("total_quantity")
        ).group_by(
            CollectionCard.set_code, CollectionCard.set_name
        ).order_by(CollectionCard.set_name).all()
        
        sets = [{
            "set_code": r.set_code,
            "set_name": r.set_name,
            "card_count": r.card_count,
            "total_quantity": r.total_quantity,
        } for r in results]
        
        return jsonify({"sets": sets})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/collection/import", methods=["POST"])
def import_collection():
    """Bulk import cards from CSV file."""
    session = get_db_session()
    if session is None:
        return jsonify({"error": "Database not configured"}), 503
    
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    
    try:
        content = file.read()
        df = pd.read_csv(io.BytesIO(content))
        
        required_cols = ["Scryfall ID"]
        if not all(col in df.columns for col in required_cols):
            return jsonify({"error": "CSV must contain 'Scryfall ID' column"}), 400
        
        client = ScryfallClient()
        imported = 0
        updated = 0
        errors = []
        
        for _, row in df.iterrows():
            scryfall_id = str(row.get("Scryfall ID", "")).strip()
            if not scryfall_id or scryfall_id == "nan":
                continue
            
            quantity = int(row.get("Quantity", 1)) if pd.notna(row.get("Quantity")) else 1
            
            try:
                existing = session.query(CollectionCard).filter_by(id=scryfall_id).first()
                
                if existing:
                    existing.quantity += quantity
                    updated += 1
                else:
                    card_data = client.get(f"/cards/{scryfall_id}")
                    
                    card = CollectionCard(
                        id=scryfall_id,
                        name=card_data.get("name", "Unknown"),
                        set_code=card_data.get("set", ""),
                        set_name=card_data.get("set_name", ""),
                        collector_number=card_data.get("collector_number", ""),
                        quantity=quantity,
                        scryfall_data=card_data,
                    )
                    session.add(card)
                    imported += 1
                
                if (imported + updated) % 50 == 0:
                    session.commit()
                    
            except Exception as e:
                errors.append(f"Card {scryfall_id}: {str(e)}")
                if len(errors) >= 10:
                    errors.append("... (more errors truncated)")
                    break
        
        session.commit()
        
        return jsonify({
            "success": True,
            "imported": imported,
            "updated": updated,
            "errors": errors if errors else None,
        })
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/cards/search", methods=["GET"])
def search_cards():
    """Search Scryfall for cards (for manual entry)."""
    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify({"error": "Search query must be at least 2 characters"}), 400
    
    try:
        client = ScryfallClient()
        data = client.get("/cards/autocomplete", params={"q": query})
        suggestions = data.get("data", [])
        
        return jsonify({"suggestions": suggestions[:10]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/cards/search/full", methods=["GET"])
def search_cards_full():
    """Search Scryfall for cards with full data."""
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Search query required"}), 400
    
    try:
        client = ScryfallClient()
        all_cards = []
        data = client.search_cards(query, page=1, unique="prints")
        
        while True:
            all_cards.extend(data.get("data", []))
            next_page = data.get("next_page")
            if not next_page:
                break
            data = client.get_url(next_page)
        
        results = []
        for card in all_cards:
            image_uris = card.get("image_uris", {})
            image_url = image_uris.get("small") or image_uris.get("normal") or ""
            
            prices = card.get("prices", {})
            price = prices.get("usd") or prices.get("usd_foil") or "0"
            
            results.append({
                "id": card.get("id"),
                "name": card.get("name"),
                "set_code": card.get("set"),
                "set_name": card.get("set_name"),
                "collector_number": card.get("collector_number"),
                "image_url": image_url,
                "price": price,
                "scryfall_data": card,
            })
        
        return jsonify({"cards": results, "total": len(results)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/collection/analyze", methods=["GET"])
def analyze_collection():
    """Analyze the database collection for missing cards per set."""
    session = get_db_session()
    if session is None:
        return jsonify({"error": "Database not configured"}), 503
    
    try:
        from sqlalchemy import func
        
        set_data = session.query(
            CollectionCard.set_code,
            CollectionCard.set_name
        ).distinct().all()
        
        if not set_data:
            return jsonify({"results": [], "message": "No cards in collection"})
        
        client = ScryfallClient()
        results = []
        
        for set_code, set_name in set_data:
            collection_ids = set(
                row[0] for row in session.query(CollectionCard.id)
                .filter(CollectionCard.set_code == set_code).all()
            )
            
            try:
                all_cards = []
                data = client.search_cards(f"set:{set_code}", page=1, unique="prints")
                
                while True:
                    all_cards.extend(data.get("data", []))
                    next_page = data.get("next_page")
                    if not next_page:
                        break
                    data = client.get_url(next_page)
                
                full_set_df = pd.DataFrame(all_cards)
                missing_df = full_set_df[~full_set_df["id"].astype(str).isin(collection_ids)]
                missing_df = missing_df.reset_index(drop=True)
                
                collection_value = 0.0
                for card in all_cards:
                    if card.get("id") in collection_ids:
                        collection_value += get_card_price(card)
                
                missing_value = calculate_missing_value(missing_df)
                
                card_kingdom_lines = []
                starcity_lines = []
                expensive_cards = []
                
                for _, row in missing_df.iterrows():
                    price = get_card_price(row.to_dict())
                    card_kingdom_lines.append(row["name"])
                    starcity_lines.append(f"{row['name']} ({row['set']})")
                    
                    if price >= 50.0:
                        row_dict = row.to_dict()
                        image_uris = row_dict.get("image_uris")
                        image_url = ""
                        if isinstance(image_uris, dict):
                            image_url = image_uris.get("normal") or image_uris.get("small") or ""
                        
                        scryfall_uri = row_dict.get("scryfall_uri", "")
                        if pd.isna(scryfall_uri):
                            scryfall_uri = ""
                        
                        expensive_cards.append({
                            "name": row["name"],
                            "price": price,
                            "collector_number": row_dict.get("collector_number", ""),
                            "rarity": row_dict.get("rarity", ""),
                            "image_url": image_url,
                            "scryfall_url": scryfall_uri,
                        })
                
                expensive_cards.sort(key=lambda x: x["price"], reverse=True)
                
                results.append({
                    "set_name": set_name,
                    "set_code": set_code,
                    "total_in_set": len(full_set_df),
                    "in_collection": len(collection_ids),
                    "missing_count": len(missing_df),
                    "collection_value": round(collection_value, 2),
                    "missing_value": missing_value,
                    "card_kingdom": "\n".join(card_kingdom_lines),
                    "starcity": "\n".join(starcity_lines),
                    "expensive_cards": expensive_cards,
                })
            except Exception as e:
                results.append({
                    "set_name": set_name,
                    "set_code": set_code,
                    "total_in_set": 0,
                    "in_collection": len(collection_ids),
                    "missing_count": 0,
                    "collection_value": 0.0,
                    "missing_value": 0.0,
                    "card_kingdom": "",
                    "starcity": "",
                    "error": str(e),
                })
        
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


with app.app_context():
    init_db()


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=debug)
