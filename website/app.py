"""
Flask web app for MTG Collection Analyzer.
Upload a collection CSV and view missing cards per set.
"""

import io
import json
import logging
import os
import sys
from functools import wraps
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template, request
from sqlalchemy import Column, Integer, String, Text, create_engine, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

# Make the project root importable so we can share scryfall.py
sys.path.insert(0, str(Path(__file__).parent.parent))
from scryfall import ScryfallClient, pull_set_cards  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit

BASE_DIR = Path(__file__).parent.parent
SET_NAMES_FILE = BASE_DIR / "set_names.json"

EXPENSIVE_CARD_THRESHOLD_USD = 25.0
SCRYFALL_BATCH_SIZE = 75  # /cards/collection accepts up to 75 IDs per request

# Optional token-based auth. Set ADMIN_TOKEN env var to enable.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

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
        logger.warning("DATABASE_URL not set. Database features disabled.")
        return False

    try:
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        logger.info("Database initialized successfully.")
        return True
    except Exception as e:
        logger.exception("Database initialization failed: %s", e)
        return False


def get_db_session():
    """Get a new database session."""
    if Session is None:
        return None
    return Session()


def require_auth(f):
    """Decorator that enforces ADMIN_TOKEN auth when the token is configured."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if ADMIN_TOKEN:
            token = request.headers.get("X-Admin-Token", "")
            if token != ADMIN_TOKEN:
                return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def get_card_price(card: dict) -> float:
    """Extract USD price from card data. Returns 0.0 if unavailable."""
    prices = card.get("prices", {})
    usd = prices.get("usd") if isinstance(prices, dict) else None
    if usd:
        try:
            return float(usd)
        except (ValueError, TypeError):
            pass
    return 0.0


def _safe_str(value) -> str:
    """Convert a value to string, treating NaN/None as empty string."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


def _build_set_result(
    edition_name: str,
    full_set_df: pd.DataFrame,
    collection_ids: set,
    collection_count: int,
) -> dict:
    """Build the per-set result dict shared by upload and analyze endpoints."""
    missing_df = full_set_df[~full_set_df["id"].astype(str).isin(collection_ids)].reset_index(drop=True)

    set_code = ""
    if len(missing_df) > 0:
        set_code = _safe_str(missing_df["set"].iloc[0])
    elif len(full_set_df) > 0:
        set_code = _safe_str(full_set_df["set"].iloc[0])

    # Vectorised price sums — avoids iterrows overhead
    all_records = full_set_df.to_dict("records")
    collection_value = sum(
        get_card_price(card) for card in all_records if _safe_str(card.get("id")) in collection_ids
    )

    missing_records = missing_df.to_dict("records")
    missing_value = sum(get_card_price(card) for card in missing_records)

    card_kingdom_lines = []
    starcity_lines = []
    expensive_cards = []

    for row in missing_records:
        price = get_card_price(row)
        name = _safe_str(row.get("name"))
        set_abbr = _safe_str(row.get("set"))
        card_kingdom_lines.append(name)
        starcity_lines.append(f"{name} ({set_abbr})")

        if price >= EXPENSIVE_CARD_THRESHOLD_USD:
            image_uris = row.get("image_uris")
            image_url = ""
            if isinstance(image_uris, dict):
                image_url = image_uris.get("normal") or image_uris.get("small") or ""

            expensive_cards.append({
                "name": name,
                "price": price,
                "collector_number": _safe_str(row.get("collector_number")),
                "rarity": _safe_str(row.get("rarity")),
                "image_url": image_url,
                "scryfall_url": _safe_str(row.get("scryfall_uri")),
            })

    expensive_cards.sort(key=lambda x: x["price"], reverse=True)

    return {
        "set_name": edition_name,
        "set_code": set_code,
        "total_in_set": len(full_set_df),
        "in_collection": collection_count,
        "missing_count": len(missing_df),
        "collection_value": round(collection_value, 2),
        "missing_value": round(missing_value, 2),
        "card_kingdom": "\n".join(card_kingdom_lines),
        "starcity": "\n".join(starcity_lines),
        "expensive_cards": expensive_cards,
    }


def process_uploaded_csv(file_content: bytes) -> list[pd.DataFrame]:
    """Load CSV from uploaded bytes, split by edition."""
    df = pd.read_csv(io.BytesIO(file_content))
    return [group.reset_index(drop=True) for _, group in df.groupby("Edition Name")]


def get_missing_cards_from_upload(file_content: bytes) -> list[dict]:
    """Process uploaded CSV and return missing cards info per set."""
    collection_dfs = process_uploaded_csv(file_content)
    results = []

    for collection_df in collection_dfs:
        edition_name = collection_df["Edition Name"].iloc[0]
        collection_ids = set(collection_df["Scryfall ID"].dropna().astype(str))

        try:
            full_set_df = pull_set_cards(edition_name)
            results.append(_build_set_result(edition_name, full_set_df, collection_ids, len(collection_df)))
        except Exception as e:
            logger.exception("Error processing set %s", edition_name)
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
        logger.exception("Error in /upload")
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
        logger.exception("Error in /set/%s", set_code)
        return jsonify({"error": str(e)}), 400


# Collection API endpoints

@app.route("/api/collection", methods=["GET"])
@require_auth
def get_collection():
    """Get cards in the collection. Supports ?set= and ?search= params."""
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
        logger.exception("Error in GET /api/collection")
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/collection", methods=["POST"])
@require_auth
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
        logger.exception("Error in POST /api/collection")
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/collection/<card_id>", methods=["PUT"])
@require_auth
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
        logger.exception("Error in PUT /api/collection/%s", card_id)
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/collection/<card_id>", methods=["DELETE"])
@require_auth
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
        logger.exception("Error in DELETE /api/collection/%s", card_id)
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/collection/sets", methods=["GET"])
@require_auth
def get_collection_sets():
    """Get list of sets in the collection."""
    session = get_db_session()
    if session is None:
        return jsonify({"error": "Database not configured"}), 503

    try:
        results = session.query(
            CollectionCard.set_code,
            CollectionCard.set_name,
            func.count(CollectionCard.id).label("card_count"),
            func.sum(CollectionCard.quantity).label("total_quantity"),
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
        logger.exception("Error in GET /api/collection/sets")
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/collection/import", methods=["POST"])
@require_auth
def import_collection():
    """Bulk import cards from CSV file.

    Batches Scryfall lookups using /cards/collection (up to 75 IDs per request)
    instead of one API call per card, dramatically reducing import time.
    """
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

        if "Scryfall ID" not in df.columns:
            return jsonify({"error": "CSV must contain 'Scryfall ID' column"}), 400

        # Build {scryfall_id: quantity} from CSV, merging duplicate rows
        id_qty: dict[str, int] = {}
        for _, row in df.iterrows():
            scryfall_id = str(row.get("Scryfall ID", "")).strip()
            if not scryfall_id or scryfall_id == "nan":
                continue
            quantity = int(row.get("Quantity", 1)) if pd.notna(row.get("Quantity")) else 1
            id_qty[scryfall_id] = id_qty.get(scryfall_id, 0) + quantity

        if not id_qty:
            return jsonify({"success": True, "imported": 0, "updated": 0, "errors": None})

        # Single query to find which IDs already exist
        all_ids = list(id_qty.keys())
        existing_cards = {
            card.id: card
            for card in session.query(CollectionCard).filter(CollectionCard.id.in_(all_ids)).all()
        }

        # Update existing cards
        updated = 0
        for card_id, card in existing_cards.items():
            card.quantity += id_qty[card_id]
            updated += 1

        # Batch-fetch truly new cards from Scryfall (/cards/collection, 75 per request)
        new_ids = [sid for sid in all_ids if sid not in existing_cards]
        imported = 0
        errors: list[str] = []
        client = ScryfallClient()

        for i in range(0, len(new_ids), SCRYFALL_BATCH_SIZE):
            batch = new_ids[i:i + SCRYFALL_BATCH_SIZE]
            try:
                fetched = client.get_cards_collection(batch)
            except Exception as e:
                logger.exception("Batch fetch failed for IDs starting with %s", batch[:3])
                errors.extend(f"Card {sid}: batch fetch failed" for sid in batch)
                if len(errors) >= 10:
                    errors.append("... (more errors truncated)")
                    break
                continue

            fetched_ids = {card_data.get("id") for card_data in fetched}
            for card_data in fetched:
                sid = card_data.get("id")
                session.add(CollectionCard(
                    id=sid,
                    name=card_data.get("name", "Unknown"),
                    set_code=card_data.get("set", ""),
                    set_name=card_data.get("set_name", ""),
                    collector_number=card_data.get("collector_number", ""),
                    quantity=id_qty.get(sid, 1),
                    scryfall_data=card_data,
                ))
                imported += 1

            for sid in batch:
                if sid not in fetched_ids:
                    errors.append(f"Card {sid}: not found on Scryfall")

        session.commit()
        return jsonify({
            "success": True,
            "imported": imported,
            "updated": updated,
            "errors": errors if errors else None,
        })
    except Exception as e:
        session.rollback()
        logger.exception("Error in POST /api/collection/import")
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/cards/search", methods=["GET"])
def search_cards():
    """Search Scryfall for cards (autocomplete)."""
    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify({"error": "Search query must be at least 2 characters"}), 400

    try:
        client = ScryfallClient()
        data = client.get("/cards/autocomplete", params={"q": query})
        return jsonify({"suggestions": data.get("data", [])[:10]})
    except Exception as e:
        logger.exception("Error in GET /api/cards/search")
        return jsonify({"error": str(e)}), 500


@app.route("/api/cards/search/full", methods=["GET"])
def search_cards_full():
    """Search Scryfall for cards with full data (all pages)."""
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
        logger.exception("Error in GET /api/cards/search/full")
        return jsonify({"error": str(e)}), 500


@app.route("/api/collection/analyze", methods=["GET"])
@require_auth
def analyze_collection():
    """Analyze the database collection for missing cards per set."""
    session = get_db_session()
    if session is None:
        return jsonify({"error": "Database not configured"}), 503

    try:
        set_data = session.query(
            CollectionCard.set_code,
            CollectionCard.set_name,
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
                full_set_df = pull_set_cards(set_name, client=client)
                results.append(_build_set_result(set_name, full_set_df, collection_ids, len(collection_ids)))
            except Exception as e:
                logger.exception("Error fetching set %s (%s)", set_name, set_code)
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
        logger.exception("Error in GET /api/collection/analyze")
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


with app.app_context():
    init_db()


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=debug)
