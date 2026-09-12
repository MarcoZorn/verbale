"""A local web page for reading back meetings.

Standard library only, bound to the loopback interface, no external assets.
It is a viewer over the same directory the CLI writes, so there is no state
here that can get out of step with the files.
"""
import json
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

WEB = Path(__file__).parent / "web"


def build_handler(cfg, store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "verbale"

        def log_message(self, *a):
            pass  # the CLI prints what matters; access logs are noise

        def send_json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, path, ctype=None):
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type",
                             ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            route = unquote(urlparse(self.path).path)
            try:
                if route in ("/", "/index.html"):
                    return self.send_file(WEB / "index.html", "text/html; charset=utf-8")

                if route == "/api/meetings":
                    return self.send_json([m.brief() for m in store.all()])

                if route.startswith("/api/meetings/"):
                    rest = route[len("/api/meetings/"):]
                    parts = rest.split("/")
                    meeting = store.get(parts[0])
                    if len(parts) == 1:
                        return self.send_json({
                            "meta": meeting.meta(),
                            "brief": meeting.brief(),
                            "segments": meeting.segments(),
                            "notes": meeting.summary(),
                        })
                    if len(parts) == 2 and parts[1] in ("you.wav", "them.wav"):
                        # Resolved against the meeting directory and checked,
                        # so a crafted id cannot walk out of the data dir.
                        audio = (meeting.path / parts[1]).resolve()
                        if not str(audio).startswith(str(cfg.root.resolve())) or not audio.exists():
                            return self.send_json({"error": "not found"}, 404)
                        return self.send_file(audio, "audio/wav")

                return self.send_json({"error": "not found"}, 404)
            except KeyError as e:
                return self.send_json({"error": str(e.args[0])}, 404)
            except BrokenPipeError:
                pass
            except Exception as e:  # a viewer should never take the server down
                return self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    return Handler


def serve(cfg, store, host="127.0.0.1", port=7777, open_browser=True):
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: binding to {host} exposes your meetings on the network")
    httpd = ThreadingHTTPServer((host, port), build_handler(cfg, store))
    url = f"http://{host}:{port}/"
    print(f"\n  verbale is reading {cfg.root}\n  {url}\n\n  ctrl-c to stop\n")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
