"""Scroll a real Chaoxing reading page so its own script can report time.

``/multimedia/readlog`` answers ``{}`` whether or not the platform credits the
heartbeat. The approach that matches web-read-tool is to keep the book page
open and change its scroll position.
"""

import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit
import urllib.request
import re


_HOST = re.compile(r"mooc\d+(?:-\d+|-ans)?\.chaoxing\.com\Z", re.I)
_BOOK_PATH = re.compile(r"/mooc-ans/(?:course|zt)/\d{1,20}\.html\Z", re.I)
_NODE_PATH = re.compile(r"/mooc-ans/ztnodedetailcontroller/visitnodedetail\Z", re.I)
_STATE_JS = (
    "(() => {"
    "const link = document.querySelector(\".cell.js-cell a[href*='ztnodedetailcontroller/visitnodedetail']\")"
    " || document.querySelector(\"a[href*='ztnodedetailcontroller/visitnodedetail']\");"
    "return {ready: document.readyState, href: location.href,"
    "hasBox: !!document.querySelector('#courseMainBox'),"
    "chapter: link ? link.href : ''};"
    "})()"
)
_MAX_MESSAGE = 2_000_000


def allow_reading_url(value):
    """Return a canonical reading-page URL, or reject anything else."""
    if not isinstance(value, str) or not value or len(value) > 16384:
        raise ValueError("阅读资源地址无效")
    if re.search(r"[\x00-\x20\x7f\\]", value):
        raise ValueError("阅读资源地址无效")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ValueError("阅读资源地址无效") from None
    host = (parts.hostname or "").lower()
    if (parts.scheme != "https" or not _HOST.fullmatch(host)
            or parts.username is not None or parts.password is not None
            or port not in (None, 443) or parts.fragment):
        raise ValueError("拒绝非受信任的专题阅读地址")
    if not (_BOOK_PATH.fullmatch(parts.path) or _NODE_PATH.fullmatch(parts.path)):
        raise ValueError("专题阅读地址路径不受支持")
    return urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))


def scroll_expression(step):
    """Move the reading container the same way a person scrolls the page."""
    index = int(step) % 3
    return (
        "(() => {"
        "const box = document.querySelector('#courseMainBox') || document.scrollingElement || document.documentElement;"
        "const max = Math.max(0, (box.scrollHeight || 0) - (box.clientHeight || 0));"
        f"const delta = [380, 380, -280][{index}];"
        "let top = (box.scrollTop || 0) + delta;"
        "if (max > 0) {"
        "if (top > max) top = Math.max(0, max - 280);"
        "if (top < 0) top = Math.min(max, 380);"
        "} else { top = Math.max(0, top); }"
        "box.scrollTop = top;"
        "box.dispatchEvent(new Event('scroll', {bubbles: true}));"
        "return top;"
        "})()"
    )


def browser_executable():
    """Find a normal Chrome or Edge, never a packaged headless browser."""
    override = os.environ.get("CHAOXING_BROWSER")
    if override and os.path.isfile(override) and os.path.basename(override).lower() in {"chrome.exe", "msedge.exe"}:
        return override
    candidates = (
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    )
    return next((path for path in candidates if path and os.path.isfile(path)), None)


def chrome_command(executable, port, profile):
    return [
        executable,
        f"--remote-debugging-port={int(port)}",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-allow-origins=http://127.0.0.1:{int(port)}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-extensions",
        "--new-window",
        "--window-size=1100,800",
        "about:blank",
    ]


def cdp_cookies(jar):
    """Copy only Chaoxing cookies into the reading browser."""
    items = []
    for cookie in jar:
        domain = (cookie.domain or "").lstrip(".").lower()
        if domain != "chaoxing.com" and not domain.endswith(".chaoxing.com"):
            continue
        if not cookie.name or cookie.value is None:
            continue
        rest = getattr(cookie, "_rest", {}) or {}
        item = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain or ".chaoxing.com",
            "path": cookie.path or "/",
            "secure": bool(cookie.secure),
            "httpOnly": bool(rest.get("HttpOnly") or rest.get("httponly")),
        }
        if cookie.expires:
            item["expires"] = int(cookie.expires)
        items.append(item)
    return items


def scroll_reading_page(page, seconds, on_progress=None, wait=None, check=None):
    """Change scroll position once per reporting interval and count only waited time."""
    if not 0 < float(seconds) <= 86400:
        raise ValueError("阅读时长必须大于零且不超过 24 小时")
    wait = wait or time.sleep
    check = check or (lambda: None)
    completed = 0.0
    step = 0
    if callable(on_progress):
        on_progress(0, seconds)
    while completed < seconds:
        check()
        page.evaluate(scroll_expression(step))
        step += 1
        interval = min(5.0, seconds - completed)
        wait(interval)
        completed += interval
        if callable(on_progress):
            on_progress(completed, seconds)
    return completed


def scroll_book(url, cookies, seconds, on_progress=None, wait=None, check=None, opener=None):
    """Open the book, enter a chapter when the index has no reading box, then scroll."""
    url = allow_reading_url(url)
    if not cdp_cookies(cookies):
        raise RuntimeError("阅读页缺少登录状态")
    factory = opener or ChromeReadingPage.open
    page = factory(cookies)
    try:
        page.goto(url)
        state = page.evaluate(_STATE_JS) or {}
        if not state.get("hasBox"):
            chapter = state.get("chapter") or ""
            if not chapter:
                raise RuntimeError("阅读页没有可滚动的正文")
            page.goto(allow_reading_url(chapter))
        return scroll_reading_page(
            page, seconds, on_progress=on_progress, wait=wait, check=check,
        )
    finally:
        page.close()


class CdpSocket:
    """Small DevTools client. Chrome's debugger socket is only used locally."""

    def __init__(self, ws_url, port):
        parts = urlsplit(ws_url)
        host = "127.0.0.1" if parts.hostname == "localhost" else parts.hostname
        if parts.scheme != "ws" or host != "127.0.0.1" or parts.port != int(port):
            raise RuntimeError("阅读浏览器调试地址无效")
        port = int(port)
        self._sock = socket.create_connection((host, port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        host = parts.hostname if parts.port is None else f"{parts.hostname}:{parts.port}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Origin: http://{host}\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode())
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise RuntimeError("阅读浏览器调试连接失败")
            header += chunk
            if len(header) > 65536:
                raise RuntimeError("阅读浏览器调试连接失败")
        head, self._buf = header.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise RuntimeError("阅读浏览器调试连接失败")
        self._id = 0

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass

    def call(self, method, params=None, timeout=20):
        self._id += 1
        ident = self._id
        payload = json.dumps({"id": ident, "method": method, "params": params or {}},
                             ensure_ascii=False).encode()
        self._send(1, payload)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self._recv(deadline)
            if message is None:
                continue
            data = json.loads(message)
            if data.get("id") != ident:
                continue
            if data.get("error"):
                raise RuntimeError("阅读浏览器指令失败")
            return data.get("result") or {}
        raise RuntimeError("阅读浏览器指令超时")

    def _send(self, opcode, data):
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        header = bytearray([0x80 | opcode])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(length.to_bytes(2, "big"))
        else:
            header.append(0x80 | 127)
            header.extend(length.to_bytes(8, "big"))
        header.extend(mask)
        self._sock.sendall(bytes(header) + masked)

    def _recv(self, deadline):
        fragments = []
        opcode = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._sock.settimeout(min(1, remaining))
            try:
                frame = self._read_frame()
            except socket.timeout:
                self.close()
                raise RuntimeError("阅读浏览器指令超时") from None
            kind, payload = frame
            if kind == 8:
                raise RuntimeError("阅读浏览器调试连接已关闭")
            if kind == 9:
                self._send(10, payload)
                continue
            if kind == 10:
                continue
            if kind in {0, 1}:
                if kind == 1:
                    opcode = 1
                    fragments = [payload]
                else:
                    fragments.append(payload)
                if sum(len(item) for item in fragments) > _MAX_MESSAGE:
                    raise RuntimeError("阅读浏览器返回了过大的响应")
                # The first frame is the whole message unless FIN was absent.
                # ``_read_frame`` returns only complete messages.
                if opcode == 1:
                    return b"".join(fragments)
            raise RuntimeError("阅读浏览器返回了无法识别的数据")

    def _read_exact(self, size):
        while len(self._buf) < size:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise RuntimeError("阅读浏览器调试连接已关闭")
            self._buf += chunk
        data, self._buf = self._buf[:size], self._buf[size:]
        return data

    def _read_frame(self):
        """Read one complete text message, joining continuation frames."""
        pieces = []
        text = False
        while True:
            header = self._read_exact(2)
            fin = header[0] & 0x80
            kind = header[0] & 0x0F
            masked = header[1] & 0x80
            length = header[1] & 0x7F
            if length == 126:
                length = int.from_bytes(self._read_exact(2), "big")
            elif length == 127:
                length = int.from_bytes(self._read_exact(8), "big")
            if length > _MAX_MESSAGE:
                raise RuntimeError("阅读浏览器返回了过大的响应")
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if kind == 8 and fin:
                raise RuntimeError("阅读浏览器调试连接已关闭")
            if kind == 9 and fin:
                self._send(10, payload)
                continue
            if kind == 10 and fin:
                continue
            if kind in {0, 1}:
                if kind == 1:
                    text = True
                    pieces = [payload]
                elif text:
                    pieces.append(payload)
                else:
                    raise RuntimeError("阅读浏览器返回了无法识别的数据")
                if sum(len(item) for item in pieces) > _MAX_MESSAGE:
                    raise RuntimeError("阅读浏览器返回了过大的响应")
                if fin:
                    return 1, b"".join(pieces)
                continue
            raise RuntimeError("阅读浏览器返回了无法识别的数据")



def navigation_ready(href, ready):
    """True only after the requested book or chapter has actually committed."""
    if ready != "complete" or not isinstance(href, str):
        return False
    if href in {"", "about:blank"} or href.startswith("chrome-error:"):
        return False
    allow_reading_url(href)
    return True


def _devtools_ready(log_path, process, port):
    marker = f"DevTools listening on ws://127.0.0.1:{int(port)}/"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            contents = open(log_path, encoding="utf-8", errors="replace").read()
        except OSError:
            contents = ""
        if marker in contents:
            return True
        if process.poll() is not None:
            return False
        time.sleep(0.05)
    return False


class ChromeReadingPage:
    """One temporary browser window signed in with the current account."""

    def __init__(self, process, socket_client, profile, log_file=None):
        self._process = process
        self._socket = socket_client
        self._profile = profile
        self._log = log_file

    @classmethod
    def open(cls, cookies):
        executable = browser_executable()
        if not executable:
            raise RuntimeError("未找到 Chrome 或 Edge，无法打开阅读页")
        profile = tempfile.mkdtemp(prefix="chaoxing-read-")
        process = None
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            log_path = os.path.join(profile, "devtools.log")
            log_file = open(log_path, "w", encoding="utf-8", errors="replace")
            process = subprocess.Popen(
                chrome_command(executable, port, profile),
                stdout=subprocess.DEVNULL, stderr=log_file,
            )
            process._devtools_log = log_file
            # Cookies are sent only after this process says it is listening.
            # If another program took the port, Chrome never prints this line.
            if not _devtools_ready(log_path, process, port):
                log_file.close()
                raise RuntimeError("阅读浏览器没有启动")
            version = _debugger_json(port, "/json/version") or {}
            browser_name = str(version.get("Browser") or "")
            if "Chrome" not in browser_name and "Edg" not in browser_name:
                raise RuntimeError("阅读浏览器没有启动")
            page = _page_target(port)
            client = CdpSocket(page["webSocketDebuggerUrl"], port)
            try:
                client.call("Network.enable")
                accepted = 0
                for item in cdp_cookies(cookies):
                    result = client.call("Network.setCookie", item)
                    accepted += int(result.get("success") is True)
                if not accepted:
                    raise RuntimeError("阅读页缺少登录状态")
            except BaseException:
                client.close()
                raise
            page = cls(process, client, profile, log_file)
            return page
        except BaseException:
            if process is not None and process.poll() is None:
                _stop_process(process)
            if process is not None:
                log = getattr(process, "_devtools_log", None)
                if log is not None and not log.closed:
                    log.close()
            _remove_profile(profile)
            raise

    def goto(self, url):
        url = allow_reading_url(url)
        self._socket.call("Page.navigate", {"url": url})
        deadline = time.monotonic() + 20
        last_error = None
        while time.monotonic() < deadline:
            try:
                try:
                    ready = navigation_ready(self.evaluate("location.href"), self.evaluate("document.readyState"))
                except ValueError:
                    raise RuntimeError("阅读页跳转到了不受支持的地址") from None
                if ready:
                    return
            except RuntimeError as exc:
                last_error = exc
            time.sleep(0.2)
        raise RuntimeError("阅读页打开超时") from last_error

    def evaluate(self, expression):
        result = self._socket.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True,
        })
        if result.get("exceptionDetails"):
            raise RuntimeError("阅读页脚本执行失败")
        return result.get("result", {}).get("value")

    def close(self):
        try:
            self._socket.close()
        finally:
            _stop_process(self._process)
            if self._log is not None and not self._log.closed:
                self._log.close()
            _remove_profile(self._profile)


_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _debugger_json(port, path, method="GET"):
    deadline = time.monotonic() + 15
    url = f"http://127.0.0.1:{port}{path}"
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(url, method=method)
            with _LOCAL_OPENER.open(request, timeout=0.5) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError):
            time.sleep(0.1)
    return None


def _page_target(port):
    pages = _debugger_json(port, "/json/list") or []
    if not isinstance(pages, list):
        pages = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
            return page
    created = _debugger_json(port, "/json/new?about:blank", method="PUT")
    if not isinstance(created, dict) or not created.get("webSocketDebuggerUrl"):
        created = _debugger_json(port, "/json/new?about:blank")
    if not isinstance(created, dict) or not created.get("webSocketDebuggerUrl"):
        raise RuntimeError("阅读浏览器没有打开页面")
    return created



def _remove_profile(profile):
    for _ in range(5):
        try:
            shutil.rmtree(profile)
            return
        except OSError:
            time.sleep(0.2)
    shutil.rmtree(profile, ignore_errors=True)


def _stop_process(process):
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    else:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
