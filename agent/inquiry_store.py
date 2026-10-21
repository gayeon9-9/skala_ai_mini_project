"""2단계 — 문의 원문, 선택 대상과 요청별 처리 상태를 SQLite에 보존한다.

기존 messages 테이블을 지우거나 변형하지 않고 접수 테이블만 추가한다.
request_id는 네트워크 재전송의 중복 방지 키이며 인증 토큰은 아니다.
연결 수명은 SqliteSessionStore가 소유한다. 이 저장소에서 별도로 닫지 않는다.
"""

import json
from datetime import datetime, timezone
from uuid import uuid4

from agent.inquiry_models import InquiryConflict
from agent.session_store import SqliteSessionStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class SqliteInquiryStore:
    def __init__(self, session_store: SqliteSessionStore):
        self.sessions = session_store
        # DDL 역시 기존 연결과 잠금을 공유한다. 사용자 DB를 초기화하지 않는다.
        with session_store.transaction() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS inquiries (
                inquiry_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE,
                selected_system TEXT, status TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS inquiry_requests (
                request_id TEXT PRIMARY KEY,
                inquiry_id TEXT NOT NULL REFERENCES inquiries(inquiry_id),
                message TEXT NOT NULL, status TEXT NOT NULL,
                result_json TEXT, error TEXT, run_id TEXT,
                user_message_rowid INTEGER NOT NULL,
                assistant_message_rowid INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            conn.execute("""CREATE INDEX IF NOT EXISTS requests_by_inquiry
                ON inquiry_requests(inquiry_id, user_message_rowid)""")
            # Python 잠금 외에 DB 제약도 둔다. 다른 연결이 와도 같은 문의의 작업은
            # 동시에 하나만 진행되어 대화 순서와 최종 결과가 뒤섞이지 않는다.
            conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_processing_request
                ON inquiry_requests(inquiry_id) WHERE status='processing'""")

    def begin(self, thread_id: str, selected_system: str | None,
              message: str, request_id: str) -> dict:
        """AI 실행 전에 원문을 저장한다. 같은 완료 요청 재전송은 저장 결과를 반환한다."""
        if not all(isinstance(v, str) and v.strip() for v in (thread_id, message, request_id)):
            raise ValueError("thread_id, message, request_id는 비어 있을 수 없습니다.")
        now = _now()
        with self.sessions.transaction() as conn:
            inquiry = conn.execute("SELECT * FROM inquiries WHERE thread_id=?", (thread_id,)).fetchone()
            old = conn.execute("SELECT * FROM inquiry_requests WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if (not inquiry or old["inquiry_id"] != inquiry["inquiry_id"]
                        or old["message"] != message or inquiry["selected_system"] != selected_system):
                    raise InquiryConflict("같은 요청 ID를 다른 문의 내용에 사용할 수 없습니다.")
                if old["status"] == "completed":
                    return self._begin_result(conn, inquiry, old, replayed=True)
                if old["status"] == "processing":
                    raise InquiryConflict("이 요청은 이미 처리 중입니다. 완료 후 다시 확인해주세요.")
            if inquiry and inquiry["selected_system"] != selected_system:
                raise InquiryConflict("다른 시스템은 새 문의에서 선택해주세요.")
            if inquiry and conn.execute(
                "SELECT 1 FROM inquiry_requests WHERE inquiry_id=? AND status='processing'",
                (inquiry["inquiry_id"],),
            ).fetchone():
                raise InquiryConflict("이 문의의 이전 요청이 처리 중입니다.")
            if not inquiry:
                conn.execute(
                    "INSERT INTO inquiries VALUES (?,?,?,?,?,?,?)",
                    (str(uuid4()), thread_id, selected_system, "processing", "{}", now, now),
                )
                inquiry = conn.execute("SELECT * FROM inquiries WHERE thread_id=?", (thread_id,)).fetchone()
            if old:
                # 실패 원문은 이미 존재한다. 재시도에서 원문을 또 넣지 않는다.
                # 후속 대화까지 진행된 과거 요청을 다시 실행하면 문맥이 역전되므로 거절한다.
                if conn.execute(
                    "SELECT 1 FROM inquiry_requests WHERE inquiry_id=? AND user_message_rowid>?",
                    (inquiry["inquiry_id"], old["user_message_rowid"]),
                ).fetchone():
                    raise InquiryConflict("후속 대화가 있는 이전 요청은 새 요청 ID로 다시 문의해주세요.")
                conn.execute(
                    "UPDATE inquiry_requests SET status='processing', error=NULL, updated_at=? WHERE request_id=?",
                    (now, request_id),
                )
            else:
                cursor = conn.execute(
                    "INSERT INTO messages (thread_id,role,content,created) VALUES (?,?,?,?)",
                    (thread_id, "user", message, now),
                )
                conn.execute("""INSERT INTO inquiry_requests
                    (request_id,inquiry_id,message,status,user_message_rowid,created_at,updated_at)
                    VALUES (?,?,?,'processing',?,?,?)""",
                    (request_id, inquiry["inquiry_id"], message, cursor.lastrowid, now, now))
            conn.execute("UPDATE inquiries SET status='processing',updated_at=? WHERE inquiry_id=?",
                         (now, inquiry["inquiry_id"]))
            request = conn.execute("SELECT * FROM inquiry_requests WHERE request_id=?", (request_id,)).fetchone()
            return self._begin_result(conn, inquiry, request, replayed=False)

    @staticmethod
    def _begin_result(conn, inquiry, request, *, replayed: bool) -> dict:
        # 현재 질문은 graph.query로 한 번만 전달된다. history에 또 넣지 않는다.
        rows = conn.execute(
            "SELECT role,content FROM messages WHERE thread_id=? AND rowid<? ORDER BY rowid",
            (inquiry["thread_id"], request["user_message_rowid"]),
        ).fetchall()
        return {"inquiry_id": inquiry["inquiry_id"], "request_id": request["request_id"],
                "status": request["status"], "replayed": replayed,
                "result": json.loads(request["result_json"]) if replayed else None,
                "run_id": request["run_id"],
                "history": [{"role": row["role"], "content": row["content"]} for row in rows]}

    def complete(self, request_id: str, result: dict, run_id: str | None = None) -> None:
        """최종 답변·그래프 결과·완료 상태를 하나의 트랜잭션으로 확정한다."""
        if not isinstance(result.get("final"), str) or not result["final"].strip():
            raise ValueError("완료 결과에는 비어 있지 않은 final 답변이 필요합니다.")
        serialized = _json(result)  # 직렬화 실패도 부분 저장 전에 발견한다.
        now = _now()
        with self.sessions.transaction() as conn:
            request = conn.execute("SELECT * FROM inquiry_requests WHERE request_id=?", (request_id,)).fetchone()
            if not request:
                raise KeyError(request_id)
            if request["status"] == "completed":
                return  # 완료 확인/재전송으로 assistant 메시지가 중복되지 않는다.
            if request["status"] != "processing":
                raise InquiryConflict("처리 중인 요청만 완료할 수 있습니다.")
            inquiry = conn.execute("SELECT * FROM inquiries WHERE inquiry_id=?", (request["inquiry_id"],)).fetchone()
            cursor = conn.execute(
                "INSERT INTO messages (thread_id,role,content,created) VALUES (?,?,?,?)",
                (inquiry["thread_id"], "assistant", result["final"], now),
            )
            conn.execute("""UPDATE inquiry_requests SET status='completed',result_json=?,
                error=NULL,run_id=?,assistant_message_rowid=?,updated_at=? WHERE request_id=?""",
                (serialized, run_id, cursor.lastrowid, now, request_id))
            conn.execute("UPDATE inquiries SET status='completed',updated_at=? WHERE inquiry_id=?",
                         (now, request["inquiry_id"]))

    def fail(self, request_id: str, error: str, *, interrupted: bool = False) -> None:
        """실패해도 접수 원문은 남긴다. 이미 확정한 답변은 연결 종료로 취소하지 않는다."""
        status = "interrupted" if interrupted else "failed"
        now = _now()
        with self.sessions.transaction() as conn:
            request = conn.execute("SELECT * FROM inquiry_requests WHERE request_id=?", (request_id,)).fetchone()
            if not request:
                raise KeyError(request_id)
            if request["status"] != "processing":
                return
            conn.execute("UPDATE inquiry_requests SET status=?,error=?,updated_at=? WHERE request_id=?",
                         (status, str(error)[:400], now, request_id))
            conn.execute("UPDATE inquiries SET status=?,updated_at=? WHERE inquiry_id=?",
                         (status, now, request["inquiry_id"]))

    def recover_interrupted(self) -> int:
        """단일 서버 시작 시 호출: 종료 전 진행 중이던 요청을 영구 대기 상태로 두지 않는다.

        다중 워커 환경에는 lease/worker 구분이 필요하다. 이 로컬 시연은 단일 워커다.
        """
        now = _now()
        with self.sessions.transaction() as conn:
            cursor = conn.execute("""UPDATE inquiry_requests SET status='interrupted',
                error='서버 재시작으로 처리가 중단되었습니다. 같은 요청을 다시 시도할 수 있습니다.',
                updated_at=? WHERE status='processing'""", (now,))
            conn.execute("UPDATE inquiries SET status='interrupted',updated_at=? WHERE status='processing'", (now,))
            return cursor.rowcount

    def update_details(self, inquiry_id: str, details: dict) -> None:
        """구조화한 접수 필드를 보존한다. 호출자가 기존 필드와 병합한 전체 값을 전달한다."""
        serialized = _json(details)
        with self.sessions.transaction() as conn:
            cursor = conn.execute("UPDATE inquiries SET details_json=?,updated_at=? WHERE inquiry_id=?",
                                  (serialized, _now(), inquiry_id))
            if not cursor.rowcount:
                raise KeyError(inquiry_id)

    def get_thread(self, thread_id: str) -> dict | None:
        with self.sessions.lock:
            row = self.sessions.conn.execute("SELECT * FROM inquiries WHERE thread_id=?", (thread_id,)).fetchone()
            return self._detail(row) if row else None

    def get_inquiry(self, inquiry_id: str) -> dict | None:
        with self.sessions.lock:
            row = self.sessions.conn.execute("SELECT * FROM inquiries WHERE inquiry_id=?", (inquiry_id,)).fetchone()
            return self._detail(row) if row else None

    @staticmethod
    def _inquiry(row) -> dict:
        data = dict(row)
        data["details"] = json.loads(data.pop("details_json"))
        return data

    def _detail(self, row) -> dict:
        data = self._inquiry(row)
        requests = self.sessions.conn.execute(
            "SELECT * FROM inquiry_requests WHERE inquiry_id=? ORDER BY user_message_rowid", (row["inquiry_id"],),
        ).fetchall()
        data["requests"] = []
        data["latest_result"] = None
        for request in requests:
            item = dict(request)
            serialized = item.pop("result_json")
            item["result"] = json.loads(serialized) if serialized else None
            if item["result"] is not None:
                data["latest_result"] = item["result"]
            data["requests"].append(item)
        data["messages"] = self.sessions.load(row["thread_id"])
        return data

    def list_inquiries(self, limit: int = 100, offset: int = 0,
                       status: str | None = None) -> list[dict]:
        limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
        with self.sessions.lock:
            rows = self.sessions.conn.execute(
                "SELECT * FROM inquiries WHERE (? IS NULL OR status=?) ORDER BY updated_at DESC,inquiry_id LIMIT ? OFFSET ?",
                (status, status, limit, offset),
            ).fetchall()
            return [self._inquiry(row) for row in rows]
