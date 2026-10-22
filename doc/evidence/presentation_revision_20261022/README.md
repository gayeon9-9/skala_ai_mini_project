# 발표용 최신 테스트 근거 (2026-10-22)

`tests/test_inquiry_store.py`의 기존 스키마 준비 연결을 `closing`으로 닫도록 수정했다.
SQLite 연결의 문맥 관리자는 커밋/롤백만 수행하므로 명시적 연결 종료가 필요하다.
Windows 파일 잠금 문제를 예방하는 수정이며 이번 재실행 환경은 macOS다.

수정 전 기준 fa5b38f: 236 tests + 173 subtests 통과, 11.16초 (`pytest.log`).
연결 종료 수정 후: 236 tests + 173 subtests 통과, 11.16초 (`pytest_after_close.log`).
시간이 같은 것은 이번 두 실행에서 관측된 값이며 성능 개선을 주장하지 않는다.
`pytest_after_close.xml`은 수정 후 JUnit 원본이다. 실제 LLM 정확도를 평가한 결과가 아니다.

발표용 이미지는 실제 로그를 HTML로 표시한 화면 캡처이며 터미널 앱 자체의 캡처가 아니다.
기존 Windows 실행 기록이나 10/21 UI 캡처를 최신 결과로 변경하지 않았다.
