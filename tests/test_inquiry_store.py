"""실제 SQLite 임시 파일로 보존·중복 방지·부분 저장 취소를 확인한다."""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from agent.inquiry_models import InquiryConflict
from agent.inquiry_store import SqliteInquiryStore
from agent.session_store import SqliteSessionStore


class InquiryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "session.db"
        self.sessions = SqliteSessionStore(self.path)
        self.store = SqliteInquiryStore(self.sessions)
        self.addCleanup(lambda: self.sessions.close())

    def begin(self, request="r1", message="로그인 후 화면이 넘어가지 않아요", thread="t1", system="auth-service"):
        return self.store.begin(thread, system, message, request)

    def test_original_saved_before_model_and_current_question_not_in_history(self):
        started = self.begin()
        self.assertEqual(started["history"], [])
        self.assertEqual(self.sessions.load("t1"), [{"role": "user", "content": "로그인 후 화면이 넘어가지 않아요"}])
        self.assertEqual(self.store.get_thread("t1")["status"], "processing")

    def test_reopen_keeps_same_inquiry_system_and_both_messages(self):
        started = self.begin()
        self.store.complete("r1", {"final": "기초 점검을 확인했어요", "events": []}, run_id="run-1")
        self.sessions.close()
        self.sessions = SqliteSessionStore(self.path)
        self.store = SqliteInquiryStore(self.sessions)
        restored = self.store.get_thread("t1")
        self.assertEqual(restored["inquiry_id"], started["inquiry_id"])
        self.assertEqual(restored["selected_system"], "auth-service")
        self.assertEqual([m["role"] for m in restored["messages"]], ["user", "assistant"])
        self.assertEqual(restored["requests"][0]["run_id"], "run-1")

    def test_completed_retry_returns_same_result_without_new_messages(self):
        self.begin()
        result = {"final": "샘플 지표를 확인했어요", "events": [{"type": "Check"}]}
        self.store.complete("r1", result)
        replay = self.begin()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["result"], result)
        self.store.complete("r1", result)
        self.assertEqual(len(self.sessions.load("t1")), 2)

    def test_request_id_cannot_be_reused_for_other_payload_or_thread(self):
        self.begin()
        for values in [dict(message="다른 문의"), dict(thread="t2"), dict(system="web-01")]:
            with self.subTest(values=values), self.assertRaises(InquiryConflict):
                self.begin(**values)
        self.assertIsNone(self.store.get_thread("t2"))

    def test_selected_system_immutable_but_new_thread_is_independent(self):
        first = self.begin()
        self.store.complete("r1", {"final": "답변"})
        with self.assertRaises(InquiryConflict):
            self.begin(request="r2", system="web-01")
        second = self.begin(request="r2", thread="t2", system="web-01")
        self.assertNotEqual(first["inquiry_id"], second["inquiry_id"])

    def test_failed_retry_keeps_original_once_and_carries_previous_history(self):
        self.begin()
        self.store.complete("r1", {"final": "추가 정보를 알려주세요"})
        second = self.begin(request="r2", message="오전 9시부터예요")
        self.store.fail("r2", "외부 조회 실패")
        self.assertEqual(self.store.get_thread("t1")["status"], "failed")
        retried = self.begin(request="r2", message="오전 9시부터예요")
        self.assertEqual(retried["history"], second["history"])
        self.assertEqual(len(retried["history"]), 2)
        self.assertEqual(len(self.sessions.load("t1")), 3)
        self.store.complete("r2", {"final": "시각을 기록했어요"})
        self.assertEqual(len(self.sessions.load("t1")), 4)

    def test_restart_recovery_retains_original_and_allows_retry(self):
        self.begin()
        self.assertEqual(self.store.recover_interrupted(), 1)
        restored = self.store.get_thread("t1")
        self.assertEqual(restored["status"], "interrupted")
        self.assertEqual(restored["requests"][0]["status"], "interrupted")
        self.assertEqual(self.store.recover_interrupted(), 0)
        self.assertFalse(self.begin()["replayed"])
        self.assertEqual(len(self.sessions.load("t1")), 1)

    def test_complete_transaction_rolls_back_insert_when_result_update_fails(self):
        self.begin()
        with self.sessions.transaction() as conn:
            conn.execute("""CREATE TRIGGER fail_result_update BEFORE UPDATE OF result_json
                ON inquiry_requests BEGIN SELECT RAISE(ABORT, 'simulate disk failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.complete("r1", {"final": "저장되면 안 되는 답변"})
        self.assertEqual(len(self.sessions.load("t1")), 1)
        self.assertEqual(self.store.get_thread("t1")["status"], "processing")
        self.store.fail("r1", "최종 저장 실패")
        self.assertEqual(self.store.get_thread("t1")["status"], "failed")

    def test_invalid_result_cannot_mark_request_completed(self):
        self.begin()
        for result in [{"final": ""}, {}, {"final": "답변", "invalid": object()}]:
            with self.subTest(result=result), self.assertRaises((ValueError, TypeError)):
                self.store.complete("r1", result)
        self.assertEqual(self.store.get_thread("t1")["status"], "processing")
        self.assertEqual(len(self.sessions.load("t1")), 1)

    def test_concurrent_requests_have_one_active_request_and_one_original(self):
        def attempt(request):
            try:
                self.begin(request=request)
                return "started"
            except InquiryConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ["r1", "r2"]))
        self.assertEqual(sorted(results), ["conflict", "started"])
        self.assertEqual(len(self.sessions.load("t1")), 1)

    def test_late_disconnect_does_not_undo_completed_result(self):
        self.begin()
        self.store.complete("r1", {"final": "완료"})
        self.store.fail("r1", "연결 종료", interrupted=True)
        self.assertEqual(self.store.get_thread("t1")["status"], "completed")

    def test_legacy_messages_and_no_selection_are_preserved(self):
        self.sessions.append("legacy", "user", "기존 문의")
        self.sessions.append("legacy", "assistant", "기존 답변")
        started = self.begin(request="legacy-r", thread="legacy", system=None)
        self.assertEqual(len(started["history"]), 2)
        self.assertIsNone(self.store.get_thread("legacy")["selected_system"])
        self.assertEqual(len(self.sessions.load("legacy")), 3)

    def test_detail_and_list_use_saved_values_and_support_status_filter(self):
        started = self.begin()
        details = {"symptom": {"state": "known", "value": "로그인 화면 멈춤"}}
        self.store.update_details(started["inquiry_id"], details)
        self.store.complete("r1", {"final": "기록했어요", "selected_system": "auth-service"})
        self.begin(request="r2", thread="t2", system="web-01")
        item = self.store.get_inquiry(started["inquiry_id"])
        self.assertEqual(item["details"], details)
        self.assertEqual(item["latest_result"]["final"], "기록했어요")
        self.assertEqual(len(self.store.list_inquiries()), 2)
        self.assertEqual(len(self.store.list_inquiries(status="completed")), 1)
        self.assertEqual(len(self.store.list_inquiries(limit=1, offset=1)), 1)
        self.assertIsNone(self.store.get_inquiry("missing"))

    def test_old_schema_migrates_without_rewriting_message_table(self):
        legacy_path = Path(self.temp.name) / "old.db"
        # SQLite의 conn 문맥은 커밋/롤백만 수행하고 연결은 닫지 않는다.
        # Windows에서도 임시 DB를 지울 수 있도록 트랜잭션 뒤 연결을 닫는다.
        with closing(sqlite3.connect(legacy_path)) as conn, conn:
            conn.execute("CREATE TABLE messages (thread_id TEXT, role TEXT, content TEXT, created TEXT)")
            conn.execute("INSERT INTO messages VALUES ('old','user','이전 기록','2026-01-01')")
        old_sessions = SqliteSessionStore(legacy_path)
        self.addCleanup(old_sessions.close)
        SqliteInquiryStore(old_sessions)
        self.assertEqual(old_sessions.load("old"), [{"role": "user", "content": "이전 기록"}])
        self.assertEqual([row[1] for row in old_sessions.conn.execute("PRAGMA table_info(messages)")],
                         ["thread_id", "role", "content", "created"])


if __name__ == "__main__":
    unittest.main()
