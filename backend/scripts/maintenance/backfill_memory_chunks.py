"""
Rebuild user_memory_chunks from user_memory_items (RAG backfill).

Usage (from backend/):
  python scripts/maintenance/backfill_memory_chunks.py           # all users
  python scripts/maintenance/backfill_memory_chunks.py 42         # one user id
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.logger import logger
from app.db.models import User
from app.db.session import SessionLocal
from app.services.autofill.memory_chunk_indexer import MemoryChunkIndexer


def main() -> None:
    db = SessionLocal()
    try:
        if len(sys.argv) > 1:
            uid = int(sys.argv[1])
            n = MemoryChunkIndexer.reindex_all_memory_items_for_user(db, uid)
            db.commit()
            print(f"User {uid}: indexed {n} chunks")
            return
        users = db.query(User.id).order_by(User.id.asc()).all()
        total = 0
        for (user_id,) in users:
            n = MemoryChunkIndexer.reindex_all_memory_items_for_user(db, user_id)
            total += n
            logger.info(f"backfill user={user_id} chunks={n}")
        db.commit()
        print(f"Done: {len(users)} users, {total} chunks total")
    except Exception as exc:
        db.rollback()
        print(f"Failed: {exc}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
