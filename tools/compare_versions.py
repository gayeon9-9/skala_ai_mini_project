"""수정 전/후 커밋을 같은 fake 모델과 실제 MCP로 실행해 비교하는 재현 도구.

실행 예시(서버용 터미널은 유지):
  .venv/bin/python tools/compare_versions.py serve --runtime-dir /tmp/agent-compare-01
  .venv/bin/python tools/compare_versions.py check --output /tmp/comparison.json \
      --manifest /tmp/agent-compare-01/manifest.json

기존 실행 스크립트를 쓰지 않으므로 작업 폴더의 .env/session.db와 기존 서버를
건드리지 않는다. 종료는 Ctrl+C이며, 직접 시작한 프로세스만 정리한다.
"""
import argparse
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tarfile
import time
from datetime import datetime

import httpx

ROOT = Path(__file__).absolute().parent.parent
PYTHON = ROOT / ".venv" / "bin" / "python"  # resolve()하면 venv 심볼릭 링크가 풀린다.
PORTS = {"before": (18100, 18600), "after": (18101, 18601)}


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def stop_children(children):
    # 사용자 서버의 포트/PID를 찾아 종료하지 않고 여기서 만든 자식만 대상으로 삼는다.
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()  # uvicorn이 lifespan을 종료하며 MCP와 SQLite를 닫게 한다.
    for child in reversed(children):
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # 정상 종료가 막혔을 때만 직접 만든 프로세스 그룹을 강제 정리한다.
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


def serve(args):
    runtime = Path(args.runtime_dir).expanduser().absolute()
    runtime.mkdir(parents=True, exist_ok=True)
    refs = {label: git("rev-parse", "--verify", f"{getattr(args, label)}^{{commit}}").decode().strip()
            for label in PORTS}
    # 사용 중인 비교 포트를 재사용하지 않는다. 기존 프로세스를 종료하는 동작도 없다.
    for ports in PORTS.values():
        for port in ports:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError(f"비교용 포트 {port}가 이미 사용 중입니다.")
    for label, commit in refs.items():
        snapshot = runtime / label
        snapshot.mkdir()  # 이전 결과를 덮어쓰거나 DB를 재사용하지 않도록 존재하면 중단한다.
        with tarfile.open(fileobj=io.BytesIO(git("archive", commit))) as archive:
            archive.extractall(snapshot, filter="data")
        if (snapshot / ".env").exists() or (snapshot / "session.db").exists():
            raise RuntimeError("커밋에 .env/session.db가 포함되어 있어 비교 실행을 중단합니다.")

    # 실행에 필요한 PATH/HOME 등은 유지하되 사용자 인증값은 비교 자식에 넘기지 않는다.
    secret_names = ("TOKEN", "PASSWORD", "SECRET", "API_KEY", "PRIVATE_KEY", "CREDENTIAL")
    inherited = {key: value for key, value in os.environ.items()
                 if not any(marker in key.upper() for marker in secret_names)
                 and key not in {"PYTHONPATH", "PYTHONHOME"}}
    env = {**inherited, "PYTHON_DOTENV_DISABLED": "1", "LANGSMITH_TRACING": "false",
           "LANGCHAIN_TRACING_V2": "false", "LLM_BACKEND": "fake",
           "SUPERVISOR_VERSION": "v2", "PYTHONDONTWRITEBYTECODE": "1"}
    packages = ["fastapi", "uvicorn", "langgraph", "langchain-core", "langsmith",
                "fastmcp", "mcp", "httpx", "python-dotenv", "pydantic"]
    versions = subprocess.check_output(
        [str(PYTHON), "-c", "import json,sys; from importlib.metadata import version; "
         f"print(json.dumps({{p:version(p) for p in {packages!r}}} | {{'python':sys.version}}))"],
        env=env, text=True)
    manifest = {"created_at": datetime.now().astimezone().isoformat(), "commits": refs,
                "dependencies": json.loads(versions), "mode": "fake LLM + actual MCP subprocesses",
                "ports": {label: {"backend": pair[0], "proxy": pair[1]} for label, pair in PORTS.items()},
                "note": "샘플 CPU/메모리에는 무작위 변동이 있습니다. 실제 장애 진단·실모델 품질·속도 평가는 아닙니다."}
    children, logs = [], []
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        for label, (backend, proxy) in PORTS.items():
            for name, port in (("backend", backend), ("proxy", proxy)):
                module = "server" if name == "backend" else "web_demo_server"
                # 앱 파일은 변경하지 않는다. 프록시 연결 주소만 실행 시 비교 포트로 지정한다.
                code = f"import {module} as app_module; import uvicorn; "
                if name == "proxy":
                    code += f"app_module.BACKEND='http://127.0.0.1:{backend}'; "
                code += f"uvicorn.run(app_module.app, host='127.0.0.1', port={port}, log_level='warning')"
                log = (runtime / f"{label}-{name}.log").open("w", encoding="utf-8")
                logs.append(log)
                children.append(subprocess.Popen([str(PYTHON), "-c", code], cwd=runtime / label,
                                                 env=env, stdout=log, stderr=log, start_new_session=True))
        deadline, pending = time.monotonic() + 30, {p for pair in PORTS.values() for p in pair}
        with httpx.Client(timeout=0.5, trust_env=False) as client:
            while pending and time.monotonic() < deadline:
                if any(child.poll() is not None for child in children):
                    raise RuntimeError(f"서버가 시작 중 종료됐습니다. 로그: {runtime}")
                for port in list(pending):
                    try:
                        result = client.get(f"http://127.0.0.1:{port}/health")
                        if result.status_code == 200 and result.json().get("llm_backend") == "fake":
                            pending.remove(port)
                    except (httpx.RequestError, ValueError):
                        pass
                if pending:
                    time.sleep(0.2)
        if pending:
            raise RuntimeError(f"30초 안에 준비되지 않은 포트: {sorted(pending)}; 로그: {runtime}")
        save_json(args.manifest or runtime / "manifest.json", manifest)
        print(f"READY before=http://127.0.0.1:18600 after=http://127.0.0.1:18601\n로그/DB: {runtime}", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise RuntimeError(f"비교 서버가 종료됐습니다. 로그: {runtime}")
    except KeyboardInterrupt:
        print("비교용 자식 서버를 종료합니다.", flush=True)
    finally:
        stop_children(children)
        for log in logs:
            log.close()


def observe(client, base_url, method, payload):
    path = "/chat/stream" if method == "GET" else "/chat"
    response = client.request(method, base_url + path, **({"params": payload} if method == "GET" else {"json": payload}))
    events, reply, metadata = [], None, {}
    try:
        body = response.json()
        events, reply, metadata = body.get("events", []), body.get("reply"), body.get("metadata", {})
    except ValueError:
        body = response.text
        for line in body.splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                event = json.loads(line[6:])
                events.append(event)
                if event.get("type") == "FinalAnswer":
                    reply = event.get("reply")
    completed = [event for event in events if event.get("type") == "TaskCompleted"]
    return {"request": {"method": method, "url": base_url + path, "payload": payload},
            "status": response.status_code, "content_type": response.headers.get("content-type"),
            "body": body, "events": events, "reply": reply,
            "route": metadata.get("route") or next((e.get("route") for e in events if e.get("type") == "RouteDecided"), None),
            "monitoring_targets": [e["target"] for e in completed if e.get("source") == "monitoring"],
            "runbooks": [e["target"] for e in completed if e.get("source") == "runbook"]}


def check(args):
    cases = [
        ("natural_language", "로그인 후 화면이 넘어가지 않아요", "auth-service"),
        ("selected_target", "auth-service 로그인 지연을 확인해줘", "auth-service"),
        ("healthy_system", "web-01 장애 점검해줘", "web-01"),
        ("blank_message", "   ", "auth-service"),
        ("blank_thread", "안녕", "auth-service"),
        ("invalid_selection", "auth-service 로그인 지연을 확인해줘", "unregistered-service"),
        ("sse_blank_message", "   ", "auth-service"),
    ]
    report = {"checked_at": datetime.now().astimezone().isoformat(), "cases": [],
              "note": "기존 화면과 개선 화면의 경로를 비교합니다. before에는 선택 기능이 없으며 invalid_selection만 추가 필드 무시를 확인합니다. 성능 벤치마크가 아닙니다."}
    if args.manifest:
        report["manifest"] = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    # 환경의 외부 프록시를 통하지 않고 비교용 localhost 서버에만 요청한다.
    with httpx.Client(timeout=120, trust_env=False) as client:
        for name, message, selected in cases:
            row = {"name": name}
            for label, (_, port) in PORTS.items():
                payload = {"message": message, "thread_id": f"compare-{name}-{time.time_ns()}"}
                if name == "blank_thread":
                    payload["thread_id"] = "   "
                if label == "after" or name == "invalid_selection":
                    payload["selected_system"] = selected
                row[label] = observe(client, f"http://127.0.0.1:{port}", "GET" if name.startswith("sse_") else "POST", payload)
            before, after = row["before"], row["after"]
            checks = {"before_status": before["status"] == 200,
                      "after_status": after["status"] == (200 if name in {"natural_language", "selected_target", "healthy_system"} else 422)}
            if name == "natural_language":
                checks.update(before_chat=before["route"] == "chat" and before["monitoring_targets"] == [],
                              after_auth=after["route"] == "incident" and after["monitoring_targets"] == ["auth-service"])
            elif name == "selected_target":
                checks.update(before_extra_target=set(before["monitoring_targets"]) == {"auth-service", "web-01"},
                              after_only_selected=after["monitoring_targets"] == ["auth-service"])
            elif name == "healthy_system":
                checks.update(before_db_runbook=before["runbooks"] == ["db-down"],
                              after_no_runbook=after["runbooks"] == [] and after["monitoring_targets"] == ["web-01"])
            elif name == "invalid_selection":
                checks["before_ignored_selection"] = set(before["monitoring_targets"]) == {"auth-service", "web-01"}
            row.update(checks=checks, passed=all(checks.values()))
            report["cases"].append(row)
            print(f"{name}: before={before['status']} after={after['status']} {'PASS' if row['passed'] else 'FAIL'}", flush=True)
    report["passed"] = all(row["passed"] for row in report["cases"])
    save_json(args.output, report)
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("serve", help="두 커밋의 격리된 비교 서버 실행")
    launch.add_argument("--before", default="ee32f7e")
    launch.add_argument("--after", default="7dd329f")
    launch.add_argument("--runtime-dir", required=True)
    launch.add_argument("--manifest")
    verify = commands.add_parser("check", help="실제 프록시 응답 비교 결과 JSON 저장")
    verify.add_argument("--output", required=True)
    verify.add_argument("--manifest")
    args = parser.parse_args()
    return serve(args) if args.command == "serve" else check(args)


if __name__ == "__main__":
    raise SystemExit(main())
