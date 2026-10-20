"""Conservative retention for Digital Signage operational files.

Dry-run is the default. The command never follows symlinks and never removes
published, scheduled, draft-referenced, or device-assigned content.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "1" if default else "0")
    if value not in {"0", "1"}:
        raise ValueError(f"{name} deve ser 0 ou 1")
    return value == "1"


def env_days(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} deve ser >= 1")
    return value


def safe_root(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Diretório de retenção ausente ou inseguro")
    return path.resolve()


def safe_file(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative:
        raise ValueError("Caminho de mídia inválido")
    candidate = root.joinpath(*relative.split("/")).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("Caminho de mídia fora da raiz autorizada")
    if candidate.is_symlink():
        raise ValueError("Link simbólico recusado")
    return candidate


def snapshot_content_ids(raw: str) -> set[int]:
    try:
        snapshot = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return set()
    return {
        int(item["content_id"])
        for item in snapshot.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("content_id"), int)
    }


def referenced_content_ids(conn) -> set[int]:
    referenced = {
        int(row[0])
        for row in conn.execute("SELECT DISTINCT content_id FROM playlist_items")
    }
    referenced.update(
        int(row[0])
        for row in conn.execute("SELECT DISTINCT content_id FROM tv_contents")
    )
    for table in ("playlist_publications", "playlist_publication_history"):
        for row in conn.execute(f"SELECT snapshot_json FROM {table}"):
            referenced.update(snapshot_content_ids(row[0]))
    return referenced


def content_files(row) -> set[str]:
    result = {str(row["filename"] or "")}
    try:
        pages = json.loads(row["pages_json"] or "[]")
    except (json.JSONDecodeError, TypeError):
        pages = []
    if isinstance(pages, list):
        result.update(str(page) for page in pages if isinstance(page, str))
    return {item.replace("\\", "/").lstrip("/") for item in result if item}


def run_retention(db_file: Path, media_dir: Path, release_dir: Path,
                  temp_dir: Path, *, now: float | None = None,
                  temp_days: int = 7, archived_days: int = 90,
                  dry_run: bool = True, delete_archived: bool = False) -> dict:
    now = time.time() if now is None else now
    media_root = safe_root(media_dir)
    safe_root(release_dir)
    temp_root = safe_root(temp_dir)
    if db_file.is_symlink() or not db_file.is_file():
        raise ValueError("Banco de retenção ausente ou inseguro")

    report = {
        "dry_run": dry_run,
        "temp_candidates": 0,
        "temp_deleted": 0,
        "archived_candidates": 0,
        "archived_deleted": 0,
        "active": 0,
        "archived": 0,
        "unreferenced": 0,
        "releases_recommended_or_in_use": 0,
        "releases_historical": 0,
    }
    temp_cutoff = now - temp_days * 86400
    for path in temp_root.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix != ".part":
            continue
        if path.stat().st_mtime >= temp_cutoff:
            continue
        report["temp_candidates"] += 1
        if not dry_run:
            path.unlink()
            report["temp_deleted"] += 1

    with closing(sqlite3.connect(db_file)) as conn:
        conn.row_factory = sqlite3.Row
        referenced = referenced_content_ids(conn)
        archived_cutoff = now - archived_days * 86400
        for row in conn.execute(
            "SELECT id, filename, pages_json, archived_at FROM contents"
        ):
            content_id = int(row["id"])
            if row["archived_at"] is None:
                report["active"] += 1
                if content_id not in referenced:
                    report["unreferenced"] += 1
                continue
            report["archived"] += 1
            if content_id in referenced or float(row["archived_at"]) >= archived_cutoff:
                continue
            report["archived_candidates"] += 1
            paths = [safe_file(media_root, item) for item in content_files(row)]
            if not dry_run and delete_archived:
                for path in paths:
                    if path.exists():
                        if not path.is_file():
                            raise ValueError("Mídia arquivada não é arquivo regular")
                        path.unlink()
                report["archived_deleted"] += 1

        protected_versions = {
            str(row[0])
            for row in conn.execute(
                "SELECT recommended_player_version FROM tv_monitor_settings WHERE id = 1"
            )
            if row[0]
        }
        protected_release_ids = {
            int(row[0]) for row in conn.execute("SELECT DISTINCT release_id FROM player_update_jobs")
        }
        for row in conn.execute("SELECT id, version, status FROM player_releases"):
            if int(row["id"]) in protected_release_ids or row["version"] in protected_versions or row["status"] == "stable":
                report["releases_recommended_or_in_use"] += 1
            else:
                report["releases_historical"] += 1

        detail = json.dumps(report, sort_keys=True, separators=(",", ":"))
        conn.execute(
            """
            INSERT INTO audit_log
            (created_at, user_id, username, action, target_type, target_id, detail, ip_address)
            VALUES (?, NULL, 'system', 'retention.run', 'system', '', ?, NULL)
            """,
            (now, detail),
        )
        conn.commit()
    return report


def main() -> None:
    import fcntl
    data_dir = Path("/var/lib/digital-signage")
    db_file = Path(os.getenv("DS_DB_FILE", str(data_dir / "digital_signage.db")))
    media_dir = Path(os.getenv("DS_MEDIA_DIR", str(data_dir / "media")))
    release_dir = Path(os.getenv("DS_RELEASE_DIR", str(data_dir / "releases")))
    temp_dir = Path(os.getenv("DS_UPLOAD_TEMP_DIR", str(data_dir / "upload-tmp")))
    lock_path = Path(os.getenv("DS_RETENTION_LOCK", "/run/digital-signage/retention.lock"))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = run_retention(
            db_file, media_dir, release_dir, temp_dir,
            temp_days=env_days("DS_RETENTION_TEMP_DAYS", 7),
            archived_days=env_days("DS_RETENTION_ARCHIVED_DAYS", 90),
            dry_run=env_bool("DS_CLEANUP_DRY_RUN", True),
            delete_archived=env_bool("DS_CLEANUP_DELETE_ARCHIVED", False),
        )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Retenção falhou: {error}", file=sys.stderr)
        raise SystemExit(1)
