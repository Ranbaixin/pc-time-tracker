"""Custom classification rules — /api/v1/classifications"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..database import get_db
from ..classifier import reload_custom_rules

router = APIRouter(prefix="/classifications", tags=["Classifications"])


class ClassificationRule(BaseModel):
    process_match: str          # Process name or domain pattern
    category: str               # Assigned category
    sub_category: str = ""      # Assigned sub-category
    is_site: bool = False       # True = match against window title (site rule)


@router.get("")
def list_rules():
    """List all user-defined classification rules."""
    db = get_db()
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM app_categories ORDER BY id").fetchall()
    return {"data": {"rules": [dict(r) for r in rows]}}


@router.post("")
def add_rule(rule: ClassificationRule):
    """Add or update a custom classification rule. Reloads classifier."""
    db = get_db()
    with db.connect() as conn:
        try:
            conn.execute(
                """INSERT OR REPLACE INTO app_categories
                   (process_match, category, sub_category, is_site)
                   VALUES (?, ?, ?, ?)""",
                (rule.process_match, rule.category, rule.sub_category,
                 1 if rule.is_site else 0),
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Reload classifier with new rules
    with db.connect() as conn:
        all_rules = [dict(r) for r in conn.execute("SELECT * FROM app_categories").fetchall()]
    reload_custom_rules(all_rules)

    return {"data": {"message": f"Rule added: {rule.process_match} → {rule.category}"}}


@router.delete("/{rule_id}")
def delete_rule(rule_id: int):
    """Delete a custom classification rule. Reloads classifier."""
    db = get_db()
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM app_categories WHERE id = ?", (rule_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Rule not found")
        conn.execute("DELETE FROM app_categories WHERE id = ?", (rule_id,))

    # Reload
    with db.connect() as conn:
        all_rules = [dict(r) for r in conn.execute("SELECT * FROM app_categories").fetchall()]
    reload_custom_rules(all_rules)

    return {"data": {"message": f"Rule #{rule_id} deleted"}}
