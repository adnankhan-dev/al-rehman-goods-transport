from pathlib import Path
from uuid import uuid4

from ..core.paths import STATIC_DIR


UPLOAD_ROOT = STATIC_DIR / "uploads"
ALLOWED_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".webp"}


def save_upload(upload, category: str):
    if upload is None:
        return None

    filename = getattr(upload, "filename", None) or ""
    if not filename.strip():
        return None

    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
        raise ValueError(f"Unsupported file type. Allowed types: {allowed}.")

    target_dir = UPLOAD_ROOT / category
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = f"{uuid4().hex}{extension}"
    target_path = target_dir / safe_name

    upload.file.seek(0)
    with target_path.open("wb") as file_handle:
        file_handle.write(upload.file.read())

    return f"/static/uploads/{category}/{safe_name}"
