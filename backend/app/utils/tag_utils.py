import fnmatch
from typing import Dict, List, Tuple

from sqlalchemy.orm import Session


def resolve_aliases(db: Session, raw_names: List[str]) -> Dict[str, Tuple[str, str]]:
    """Build an alias lookup map for a list of (already lowercased) tag names."""
    from ..models import TagAlias

    if not raw_names:
        return {}

    aliases = db.query(TagAlias).filter(TagAlias.alias_name.in_(raw_names)).all()
    return {
        a.alias_name: (a.target_tag.name, a.target_tag.category)
        for a in aliases
    }


def expand_implications(db: Session, tag_set: Dict[int, object]) -> None:
    """Recursively expand tag implications into *tag_set*, mutating it in place."""
    from ..models import TagImplication

    implications = db.query(TagImplication).all()
    if not implications:
        return

    changed = True
    while changed:
        changed = False
        current_names = {t.name for t in tag_set.values()}

        for imp in implications:
            triggered = any(t.id in tag_set for t in imp.target_tags)
            if not triggered and imp.target_tag_patterns:
                triggered = any(
                    fnmatch.fnmatch(tag_name, pattern)
                    for tag_name in current_names
                    for pattern in imp.target_tag_patterns
                )

            if triggered:
                for implied_tag in imp.implied_tags:
                    if implied_tag.id not in tag_set:
                        tag_set[implied_tag.id] = implied_tag
                        changed = True
