"""Wave 12 — 프로세스 밖(SQLite 파일)에 두는 세션 저장소.

서버가 재시작돼도(새 프로세스, 새 인스턴스) 같은 session.db 파일을 다시 열므로
대화 이력이 유지된다. in-memory dict였다면 재시작 즉시 소실됐을 상태다.
"""
import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import RLock

DB_PATH = Path(os.environ.get("SESSION_DB_PATH", Path(__file__).parent.parent / "session.db"))


class SqliteSessionStore:
    def __init__(self, db_path: Path = DB_PATH):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        # FastAPI의 서로 다른 요청이 같은 연결로 트랜잭션을 섞지 않게 한다.
        # 접수 저장소도 이 잠금/연결을 공유하므로 메시지와 처리 결과를 함께 저장한다.
        self.lock = RLock()
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            " thread_id TEXT, role TEXT, content TEXT, created TEXT)"
        )
        self.conn.commit()

    @contextmanager
    def transaction(self):
        """읽기→판정→쓰기 전체를 하나로 묶고, 실패 시 부분 저장을 취소한다."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def load(self, thread_id: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT role, content FROM messages WHERE thread_id=? ORDER BY rowid",
                (thread_id,),
            ).fetchall()
        return [{"role": r, "content": c} for r, c in rows]

    def append(self, thread_id: str, role: str, content: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO messages (thread_id, role, content, created) VALUES (?,?,?,?)",
                (thread_id, role, content, datetime.now().isoformat()),
            )

    def close(self) -> None:
        with self.lock:
            self.conn.close()
