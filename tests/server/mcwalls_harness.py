import http.client as http_client, json, socket, sqlite3, tempfile, time, typing
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_tmp_root(prefix: str = "mcwalls-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def make_web_dir(files: typing.Dict[str, str] = None) -> Path:
    d = make_tmp_root("mcwalls-web-")
    for name, body in (files or {}).items():
        (d / name).write_text(body, encoding="utf-8")
    return d


class StubTower:
    def __init__(self, state=None, exc=None):
        self.state, self.exc, self.calls = state, exc, 0

    def __call__(self):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.state


class FakeRunner:
    def __init__(self):
        self.calls = []

    def copy(self, text):
        self.calls.append(("copy", text))

    def open_url(self, url):
        self.calls.append(("open_url", url))

    def open_app(self, name):
        self.calls.append(("open_app", name))

    def notify(self, title, body):
        self.calls.append(("notify", title, body))

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]


class FakeClock:
    def __init__(self, start_ms=None):
        self.now_ms = int(time.time() * 1000) if start_ms is None else start_ms

    def __call__(self):
        return self.now_ms

    def advance(self, ms):
        self.now_ms += ms


def raw_request(port: int, raw: bytes, timeout: float = 3.0) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.sendall(raw)
        chunks = []
        try:
            while True:
                b = s.recv(65536)
                if not b:
                    break
                chunks.append(b)
        except socket.timeout:
            pass
        return b"".join(chunks)


def wait_for(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def write_pending(state_dir: Path, record: dict) -> None:
    # Atomic-enough helper for arranging pre-existing state (T6).
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "pending.json").write_text(json.dumps(record), encoding="utf-8")


def make_fixture_db(path: Path = None, *, sessions=(), inputs=(), targets=(), keep_wal=False):
    """Fixture session db with the exact schema the matcher's SQL assumes.

    sessions: (id, directory, time_created) ; inputs: (id, session_id, payload_dict, time_created)
    targets: (session_id, target_id, time_created). Returns (db_path, anchor_connection_or_None).
    keep_wal=True -> PRAGMA journal_mode=WAL and one OPEN rw connection returned as anchor
    (holds -wal alive; test must keep the anchor referenced until done and close it at teardown)."""
    db = path or make_tmp_root("mcwalls-db-") / "db.sqlite"
    conn = sqlite3.connect(db)
    if keep_wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
      CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, time_created INTEGER, directory TEXT);
      CREATE TABLE session_input (id INTEGER PRIMARY KEY, session_id TEXT, kind TEXT, payload TEXT, time_created INTEGER);
      CREATE TABLE session_target (session_id TEXT, target_id TEXT, objective TEXT, status TEXT, time_created INTEGER);
    """)
    for sid, directory, created in sessions:
        conn.execute("INSERT INTO session VALUES (?,?,?,?)", (sid, "t", created, directory))
    for iid, sid, payload, created in inputs:
        conn.execute("INSERT INTO session_input VALUES (?,?,?,?,?)", (iid, sid, "sendText", json.dumps(payload), created))
    for sid, tid, created in targets:
        conn.execute("INSERT INTO session_target VALUES (?,?,?,?,?)", (sid, tid, "obj", "open", created))
    conn.commit()
    if keep_wal:
        return db, conn          # anchor kept open -> -wal survives
    conn.close()
    return db, None


class Resp:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body.decode("utf-8"))


@contextmanager
def serve(token: str = "mcwalls-token", *, collect_state=None, runner=None, web_dir=None,
          state_dir=None, log_dir=None, db_path=None, port=0, allow_hosts=None,
          monitor_interval_s=0.05, clock=None, start_monitor=True):
    # db_path, clock, start_monitor, monitor_interval_s:
    # accepted-but-unused, # wired in later tasks (T9)
    # alongside create_server's growing signature.
    from mc_wall.server import create_server

    tmp = make_tmp_root()
    web = web_dir or make_web_dir({"index.html": "<!doctype html><title>wall</title>",
                                   "app.js": "// app", "style.css": "/* css */"})
    state = state_dir or tmp / "state"
    logs = log_dir or tmp / "logs"
    srv = create_server(token, port=port, web_dir=web, log_dir=logs,
                        allow_hosts=allow_hosts, collect_state=collect_state,
                        state_dir=state, runner=runner)  # grown per task: db_path, clock, ...
    class Handle:
        pass

    h = Handle()
    h.server, h.token, h.web_dir, h.state_dir, h.log_dir, h.tmp = srv, token, web, state, logs, tmp
    h.base = f"http://127.0.0.1:{srv.server_address[1]}"

    def http(method, path, body=None, headers=None):
        # http_client (not http.client): the nested "http" name below would
        # shadow the module import inside this closure.
        conn = http_client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        try:
            hdrs = {"Host": f"localhost:{srv.server_address[1]}"}
            hdrs.update(headers or {})
            data = None
            if body is not None:
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                hdrs.setdefault("Content-Type", "application/json")
            conn.request(method, path, body=data, headers=hdrs)
            r = conn.getresponse()
            return Resp(r.status, dict(r.getheaders()), r.read())
        finally:
            conn.close()

    h.http = http
    assert srv.ready, "server self-test failed"
    try:
        yield h
    finally:
        srv.shutdown()
        srv.server_close()
        # fd-leak guard across the suite: remove+close THIS server's RotatingFileHandler from the
        # process-global "mc_wall.server" logger (identified by baseFilename == str(logs / "wall.log")).
        import logging as _logging

        _lg = _logging.getLogger("mc_wall.server")
        for _h in [x for x in _lg.handlers if getattr(x, "baseFilename", None) == str(logs / "wall.log")]:
            _lg.removeHandler(_h)
            _h.close()
