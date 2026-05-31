from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response

from ...auth import require_admin_mode
from ...config import settings
from ...custom_themes import custom_theme_manager, MAX_CSS_BYTES, _t
from ...models import User
from ...themes import theme_registry
from ...utils.logger import logger

router = APIRouter()

def _theme_or_404(theme_id: str) -> dict:
    """Return metadata dict or raise 404."""
    all_themes = {t["id"]: t for t in custom_theme_manager.get_all()}
    if theme_id not in all_themes:
        raise HTTPException(status_code=404, detail="Custom theme not found")
    return all_themes[theme_id]

@router.get("/builtin-themes")
async def list_builtin_themes(
    current_user: User = Depends(require_admin_mode),
):
    """
    Return all built-in (non-custom) themes.
    Used to populate the backup theme dropdown in the custom theme creator.
    """
    themes = theme_registry.get_builtin_themes()
    return {"themes": [t.to_dict() for t in themes]}

@router.get("/custom-themes")
async def list_custom_themes(
    current_user: User = Depends(require_admin_mode),
):
    """Return all custom themes with their metadata and whether each is active."""
    themes = custom_theme_manager.get_all()
    current = settings.CURRENT_THEME
    for t in themes:
        t["is_active"] = (t["id"] == current)
    return {"themes": themes}

@router.post("/custom-themes")
async def create_custom_theme(
    data: dict,
    current_user: User = Depends(require_admin_mode),
):
    """
    Create a new custom theme from CSS text.

    Body: `{ "name": str, "is_dark": bool, "css": str, "backup_theme_id"?: str }`
    """
    name = (data.get("name") or "").strip()
    css = data.get("css") or ""
    is_dark = bool(data.get("is_dark", True))
    backup_theme_id = (data.get("backup_theme_id") or "default_dark").strip()

    if not name:
        raise HTTPException(status_code=400, detail="Theme name is required")
    if not css.strip():
        raise HTTPException(status_code=400, detail="CSS content is required")

    try:
        meta = custom_theme_manager.create_theme(name, is_dark, css, backup_theme_id=backup_theme_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"theme": meta}

@router.post("/custom-themes/import")
async def import_custom_theme(
    file: UploadFile = File(...),
    name: str = Form(""),
    is_dark: str = Form(""),
    current_user: User = Depends(require_admin_mode),
):
    """
    Import a custom theme from a `.blombooru-theme` bundle or a raw `.css` file.

    - Accepts `multipart/form-data` with `file`, `name`, and `is_dark` fields.
    - `name` overrides the bundle's embedded name.
    - `is_dark` must be `"true"` or `"false"`.
    """
    content_type = file.content_type or ""
    filename = file.filename or ""

    allowed_types = {
        "text/css",
        "application/zip",
        "application/octet-stream",
        "application/x-zip-compressed",
    }
    allowed_exts = {".css", ".blombooru-theme", ".zip"}
    file_ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if content_type not in allowed_types and file_ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=_t("file_type"),
        )

    data = await file.read(MAX_CSS_BYTES + 1)
    if len(data) > MAX_CSS_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {MAX_CSS_BYTES // 1024} KB size limit",
        )

    is_dark_bool = is_dark.lower() not in ("false", "0", "no")
    name_override = name.strip() or None
    # For bundles, is_dark comes from theme.json unless the client explicitly
    # sends a non-default value. We use a sentinel: if is_dark form field is
    # the empty string, treat it as "not provided".
    is_dark_override = is_dark_bool if is_dark.strip() else None

    try:
        meta = custom_theme_manager.import_bundle(
            data,
            name_override=name_override,
            is_dark_override=is_dark_override,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"theme": meta}

@router.put("/custom-themes/{theme_id}")
async def update_custom_theme(
    theme_id: str,
    data: dict,
    current_user: User = Depends(require_admin_mode),
):
    """Update a custom theme's name, is_dark flag, backup_theme_id, and/or CSS content."""
    _theme_or_404(theme_id)

    name = data.get("name")
    is_dark = data.get("is_dark")
    css = data.get("css")
    backup_theme_id = data.get("backup_theme_id")

    try:
        meta = custom_theme_manager.update_theme(
            theme_id,
            name=name,
            is_dark=is_dark,
            css_content=css,
            backup_theme_id=backup_theme_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Custom theme not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"theme": meta}

@router.delete("/custom-themes/{theme_id}")
async def delete_custom_theme(
    theme_id: str,
    current_user: User = Depends(require_admin_mode),
):
    """Delete a custom theme. Refuses if it is the currently active theme."""
    _theme_or_404(theme_id)

    if settings.CURRENT_THEME == theme_id:
        raise HTTPException(
            status_code=409,
            detail=_t("cannot_delete_active"),
        )

    try:
        custom_theme_manager.delete_theme(theme_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Custom theme not found")

    return {"message": "Theme deleted"}

@router.get("/custom-themes/{theme_id}/export")
async def export_custom_theme(
    theme_id: str,
    current_user: User = Depends(require_admin_mode),
):
    """Export a custom theme as a `.blombooru-theme` ZIP bundle containing `theme.css` and `theme.json` (metadata)."""
    _theme_or_404(theme_id)

    try:
        bundle_bytes = custom_theme_manager.export_bundle(theme_id)
    except (KeyError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Use the slugified theme name as filename
    meta = {t["id"]: t for t in custom_theme_manager.get_all()}.get(theme_id, {})
    raw_name = meta.get("name", theme_id)
    import re
    safe_name = re.sub(r"[^\w\-]", "_", raw_name)
    filename = f"{safe_name}.blombooru-theme"

    return Response(
        content=bundle_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
