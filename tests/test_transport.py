"""Tests for the Streamable HTTP transport on /mcp."""

import json
import urllib.error
import urllib.request

import pytest

from ankimcp.simple_http_server import SimpleHTTPServer
from tests.test_utils import MockAnkiInterface

# Bypass any system HTTP proxy (e.g. macOS system-wide proxy) — the server is local.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_urlopen = _opener.open


@pytest.fixture
def server():
    """Start the MCP server on an ephemeral port and yield its base URL."""
    srv = SimpleHTTPServer(MockAnkiInterface(), host="127.0.0.1", port=0)
    srv.start()
    port = srv.server.server_address[1]
    yield f"http://127.0.0.1:{port}"
    srv.stop()


def _post(url, payload, raw=None):
    """POST to /mcp and return (status, headers, parsed-or-raw body)."""
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/mcp",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with _urlopen(req) as resp:
            body = resp.read()
            return resp.status, resp.headers, body
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_initialize_request_returns_json(server):
    """A JSON-RPC request gets a 200 application/json response."""
    status, headers, body = _post(
        server, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    result = json.loads(body)
    assert result["id"] == 1
    assert "protocolVersion" in result["result"]
    assert result["result"]["serverInfo"]["name"] == "ankimcp"


def test_tools_list_request(server):
    """tools/list works over the Streamable HTTP endpoint."""
    status, _, body = _post(server, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert status == 200
    result = json.loads(body)
    assert len(result["result"]["tools"]) == 18


def test_notification_returns_202_with_empty_body(server):
    """Notifications (no id) get 202 Accepted and no response body."""
    status, _, body = _post(
        server, {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert status == 202
    assert body == b""


def test_unknown_notification_still_accepted(server):
    """Even unknown notifications are acknowledged with 202."""
    status, _, _ = _post(server, {"jsonrpc": "2.0", "method": "no/such"})
    assert status == 202


def test_invalid_json_returns_400(server):
    """Malformed JSON gets a 400 with a JSON-RPC parse error."""
    status, _, body = _post(server, None, raw=b"{not json")
    assert status == 400
    error = json.loads(body)["error"]
    assert error["code"] == -32700


def test_invalid_jsonrpc_returns_400(server):
    """A non-JSON-RPC-2.0 message gets a 400 invalid-request error."""
    status, _, body = _post(server, {"id": 1, "method": "ping"})
    assert status == 400
    assert json.loads(body)["error"]["code"] == -32600


def test_unknown_method_returns_jsonrpc_error(server):
    """Unknown methods on a request get a JSON-RPC error with 200."""
    status, _, body = _post(server, {"jsonrpc": "2.0", "id": 3, "method": "no/such"})
    assert status == 200
    assert json.loads(body)["error"]["code"] == -32601


def test_get_mcp_returns_405(server):
    """Standalone SSE streams are not offered: GET /mcp is 405."""
    req = urllib.request.Request(f"{server}/mcp", method="GET")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _urlopen(req)
    assert exc_info.value.code == 405
    assert exc_info.value.headers["Allow"] == "POST"


def test_delete_mcp_returns_405(server):
    """No sessions exist, so DELETE /mcp is 405."""
    req = urllib.request.Request(f"{server}/mcp", method="DELETE")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _urlopen(req)
    assert exc_info.value.code == 405


def test_legacy_sse_endpoints_removed(server):
    """The deprecated SSE transport endpoints no longer exist."""
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _urlopen(f"{server}/sse")
    assert exc_info.value.code == 404

    req = urllib.request.Request(
        f"{server}/messages?session_id=x", data=b"{}", method="POST"
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _urlopen(req)
    assert exc_info.value.code == 404


def test_health_check(server):
    """The /health endpoint still works."""
    with _urlopen(f"{server}/health") as resp:
        assert resp.status == 200
        assert json.loads(resp.read())["status"] == "ok"
