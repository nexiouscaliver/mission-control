"""T3 — auth hardening (Host allowlist, token), static serving, redirect/HEAD.

Every mc_wall / harness import is INSIDE the test functions, mirroring
test_mcwalls_boot.py: at RED time each test must FAIL individually (exit 1)
instead of erroring at collection (exit 2). The harness only imports mc_wall
lazily inside serve(), so deferring the harness import keeps that guarantee.
"""

import json


def test_wrong_host_403_no_cors():
    from tests.server.mcwalls_harness import serve

    with serve() as h:
        probes = [
            ("GET", "/", None),
            ("GET", f"/{h.token}/state", None),
            ("POST", f"/{h.token}/launch", {}),
        ]
        for method, path, body in probes:
            r = h.http(method, path, body=body, headers={"Host": "evil.example"})
            assert r.status == 403, (method, path, r.status)
            assert not any(k.lower().startswith("access-control") for k in r.headers)
            assert r.body.startswith(b"forbidden")


def test_localhost_and_loopback_hosts_allowed():
    from tests.server.mcwalls_harness import serve

    with serve() as h:
        port = h.server.server_address[1]
        for host in (f"localhost:{port}", f"127.0.0.1:{port}"):
            r = h.http("GET", f"/{h.token}/assets/app.js", headers={"Host": host})
            assert r.status == 200, (host, r.status)


def test_wrong_token_404_html():
    from mc_wall.server import templates
    from tests.server.mcwalls_harness import serve

    with serve() as h:
        for path in ("/wrongtoken/", "/"):
            r = h.http("GET", path)
            assert r.status == 404, (path, r.status)
            assert r.headers["Content-Type"] == "text/html; charset=utf-8"
            assert r.body == templates.load("wrong_token.html").encode("utf-8")
            assert b"mc-wall open" in r.body
            assert not any(k.lower().startswith("access-control") for k in r.headers)


def test_index_served_verbatim_no_store():
    from tests.server.mcwalls_harness import make_web_dir, serve

    index_text = "<!doctype html>\n<title>known-index-bytes</title>\n<p>exact</p>\n"
    with serve(web_dir=make_web_dir({"index.html": index_text})) as h:
        r = h.http("GET", f"/{h.token}/")
        assert r.status == 200
        assert r.headers["Content-Type"] == "text/html; charset=utf-8"
        assert r.headers["Cache-Control"] == "no-store"
        assert r.body == index_text.encode("utf-8")


def test_asset_types_and_traversal_and_extensions():
    from tests.server.mcwalls_harness import raw_request, serve

    with serve() as h:
        port = h.server.server_address[1]

        r = h.http("GET", f"/{h.token}/assets/app.js")
        assert r.status == 200
        assert r.headers["Content-Type"] == "text/javascript"
        assert r.headers["Cache-Control"] == "no-store"

        r = h.http("GET", f"/{h.token}/assets/style.css")
        assert r.status == 200
        assert r.headers["Content-Type"] == "text/css"

        (h.web_dir / "x.html").write_text("<p>x</p>", encoding="utf-8")
        r = h.http("GET", f"/{h.token}/assets/x.html")
        assert r.status == 200
        assert r.headers["Content-Type"] == "text/html; charset=utf-8"

        # Traversal: literal ../ in the raw path is never normalized away.
        r = h.http("GET", f"/{h.token}/assets/../wall.json")
        assert r.status == 404

        # Traversal: percent-encoded ../ must never be unquoted server-side.
        raw = (
            f"GET /{h.token}/assets/%2e%2e%2fwall.json HTTP/1.1\r\n"
            f"Host: localhost:{port}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        resp = raw_request(port, raw)
        status_line = resp.split(b"\r\n", 1)[0]
        assert b" 404 " in status_line, status_line

        # Extension allowlist: x.py exists on disk but is not servable.
        (h.web_dir / "x.py").write_text("print('x')\n", encoding="utf-8")
        r = h.http("GET", f"/{h.token}/assets/x.py")
        assert r.status == 404


def test_missing_web_files_404_one_line_no_traceback():
    from tests.server.mcwalls_harness import make_web_dir, serve

    with serve(web_dir=make_web_dir()) as h:  # no index.html
        log = h.log_dir / "wall.log"
        before = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        r = h.http("GET", f"/{h.token}/")
        assert r.status == 404
        parsed = json.loads(r.body.decode("utf-8"))  # JSON, not an HTML error page
        assert parsed["error"] == "not-found"
        after = log.read_text(encoding="utf-8", errors="replace")
        assert after.count("static-missing") - before.count("static-missing") == 1
        assert "Traceback" not in after


def test_redirect_and_head():
    from tests.server.mcwalls_harness import serve

    with serve() as h:
        r = h.http("GET", f"/{h.token}")
        assert r.status == 302
        assert r.headers["Location"] == f"/{h.token}/"

        index_size = (h.web_dir / "index.html").stat().st_size
        r = h.http("HEAD", f"/{h.token}/")
        assert r.status == 200
        assert r.headers["Content-Type"] == "text/html; charset=utf-8"
        assert r.headers["Cache-Control"] == "no-store"
        assert int(r.headers["Content-Length"]) == index_size
        assert r.body == b""

        r = h.http("HEAD", f"/{h.token}/assets/app.js")
        assert r.status == 200
        assert r.headers["Content-Type"] == "text/javascript"
        assert r.body == b""


def test_unknown_methods_405():
    from tests.server.mcwalls_harness import raw_request, serve

    with serve() as h:
        port = h.server.server_address[1]
        for method in ("PUT", "OPTIONS"):
            raw = (
                f"{method} /{h.token}/x HTTP/1.1\r\n"
                f"Host: localhost:{port}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            resp = raw_request(port, raw)
            head, _, body = resp.partition(b"\r\n\r\n")
            assert b" 405 " in head.split(b"\r\n", 1)[0], (method, resp[:120])
            assert not any(
                line.lower().startswith(b"access-control") for line in head.split(b"\r\n")
            )
            assert json.loads(body.decode("utf-8")) == {
                "ok": False,
                "error": "method-not-allowed",
                "message": "method not allowed",
            }
