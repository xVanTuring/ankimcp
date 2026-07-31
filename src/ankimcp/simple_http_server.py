"""MCP-compliant HTTP server for Anki using JSON-RPC 2.0 over Streamable HTTP.

Transport (MCP "Streamable HTTP" spec):
- POST /mcp: Client JSON-RPC messages. Requests get a JSON response;
  notifications/responses get HTTP 202 with no body.
- GET /mcp: Not supported (no standalone server-to-client stream) -> 405.
- DELETE /mcp: Not supported (stateless, no sessions) -> 405.

The server is stateless: no session IDs are issued, every POST /mcp is
self-contained.
"""

import asyncio
import concurrent.futures
import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any, Callable, Dict, Optional

from .anki_interface import AnkiInterface
from .tools import AVAILABLE_TOOLS, ToolExecutor

logger = logging.getLogger(__name__)

# MCP Protocol Constants
PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {
    "name": "ankimcp",
    "version": "0.1.0",
}
SERVER_CAPABILITIES = {
    "tools": {"listChanged": False},
    "resources": {"subscribe": False, "listChanged": False},
    "logging": {},
}

# JSON-RPC 2.0 Error Codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _run_on_main_thread(func: Callable[[], Any]) -> Any:
    """Run func on Anki's main thread when running inside Anki.

    Anki's collection is not thread-safe — all access must happen on the
    main thread. The HTTP server handles requests on worker threads, so
    collection work is dispatched via mw.taskman.run_on_main and the
    worker blocks until the result (or exception) comes back.

    Outside Anki (standalone/tests, where aqt is unavailable) func runs
    inline on the current thread.
    """
    try:
        from aqt import mw  # type: ignore
    except ImportError:
        return func()
    if mw is None or mw.col is None:
        return func()

    future: concurrent.futures.Future = concurrent.futures.Future()

    def wrapper() -> None:
        try:
            future.set_result(func())
        except BaseException as exc:
            future.set_exception(exc)

    mw.taskman.run_on_main(wrapper)
    return future.result()


class JSONRPCHandler:
    """Handle JSON-RPC 2.0 message parsing and formatting."""

    @staticmethod
    def parse_request(body: str) -> Dict[str, Any]:
        """Parse a JSON-RPC request from the body string."""
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise JSONRPCError(PARSE_ERROR, f"Parse error: {e}")

        if not isinstance(data, dict):
            raise JSONRPCError(INVALID_REQUEST, "Request must be an object")

        if data.get("jsonrpc") != "2.0":
            raise JSONRPCError(INVALID_REQUEST, "Must be JSON-RPC 2.0")

        if "method" not in data:
            raise JSONRPCError(INVALID_REQUEST, "Missing 'method' field")

        return data

    @staticmethod
    def success_response(request_id: Any, result: Any) -> Dict[str, Any]:
        """Create a successful JSON-RPC response."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }

    @staticmethod
    def error_response(
        request_id: Any, code: int, message: str, data: Any = None
    ) -> Dict[str, Any]:
        """Create an error JSON-RPC response."""
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": error,
        }


class JSONRPCError(Exception):
    """Exception for JSON-RPC errors."""

    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


class MCPRequestHandler(BaseHTTPRequestHandler):
    """Handle HTTP requests for the MCP Streamable HTTP transport."""

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/health":
            self._send_json_response(200, {"status": "ok", "service": "ankimcp"})
        elif self.path == "/mcp":
            # Standalone server-to-client SSE streams are not offered.
            self._send_method_not_allowed()
        else:
            self.send_error(404, "Not Found")

    def do_DELETE(self):
        """Handle DELETE requests (session termination)."""
        if self.path == "/mcp":
            # The server is stateless and issues no session IDs.
            self._send_method_not_allowed()
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        """Handle POST requests - MCP Streamable HTTP endpoint."""
        if self.path == "/mcp":
            self._handle_mcp_post()
        else:
            self.send_error(404, "Not Found")

    def _handle_mcp_post(self):
        """Handle a POST to /mcp (Streamable HTTP transport).

        - Requests (with an "id") get a 200 JSON response.
        - Notifications/responses (no "id") get 202 Accepted with no body.
        - Invalid JSON or invalid JSON-RPC gets a 400 error response.
        """
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        try:
            request = JSONRPCHandler.parse_request(body)
        except JSONRPCError as e:
            response = JSONRPCHandler.error_response(
                None, e.code, e.message, e.data
            )
            self._send_json_response(400, response)
            return

        request_id = request.get("id")
        method = request["method"]
        params = request.get("params", {})

        # Notifications and responses carry no id: accept and move on.
        if "id" not in request:
            try:
                self._handle_method(method, params)
            except JSONRPCError as e:
                logger.warning(f"Error handling notification {method}: {e.message}")
            except Exception as e:
                logger.error(f"Error handling notification {method}: {e}")
            self._send_accepted()
            return

        try:
            result = self._handle_method(method, params)
            response = JSONRPCHandler.success_response(request_id, result)
            self._send_json_response(200, response)
        except JSONRPCError as e:
            response = JSONRPCHandler.error_response(
                request_id, e.code, e.message, e.data
            )
            self._send_json_response(200, response)
        except Exception as e:
            logger.error(f"Internal error handling request: {e}")
            response = JSONRPCHandler.error_response(
                request_id, INTERNAL_ERROR, f"Internal error: {str(e)}"
            )
            self._send_json_response(200, response)

    def _handle_method(self, method: str, params: Dict[str, Any]) -> Any:
        """Route method to appropriate handler."""
        handlers = {
            "initialize": self._handle_initialize,
            "initialized": self._handle_initialized,
            "ping": self._handle_ping,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "resources/list": self._handle_resources_list,
            "resources/read": self._handle_resources_read,
            "logging/setLevel": self._handle_logging_set_level,
        }

        handler = handlers.get(method)
        if handler is None:
            raise JSONRPCError(METHOD_NOT_FOUND, f"Method not found: {method}")

        return handler(params)

    def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle initialize request."""
        client_info = params.get("clientInfo", {})
        logger.info(f"MCP client connected: {client_info.get('name', 'unknown')}")

        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": SERVER_INFO,
            "capabilities": SERVER_CAPABILITIES,
        }

    def _handle_initialized(self, params: Dict[str, Any]) -> None:
        """Handle initialized notification."""
        logger.info("MCP client initialization complete")
        return None

    def _handle_ping(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle ping request."""
        return {}

    def _handle_logging_set_level(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle logging/setLevel request.

        Allows clients to control the server's logging verbosity.
        Valid levels: debug, info, notice, warning, error, critical, alert, emergency
        """
        level = params.get("level")
        if not level:
            raise JSONRPCError(INVALID_PARAMS, "Missing 'level' parameter")

        # Map MCP log levels to Python logging levels
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "notice": logging.INFO,  # Python doesn't have NOTICE, map to INFO
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
            "alert": logging.CRITICAL,  # Python doesn't have ALERT, map to CRITICAL
            "emergency": logging.CRITICAL,  # Python doesn't have EMERGENCY
        }

        python_level = level_map.get(level.lower())
        if python_level is None:
            raise JSONRPCError(
                INVALID_PARAMS,
                f"Invalid log level: {level}. Valid levels: {', '.join(level_map.keys())}",
            )

        # Set the log level for the ankimcp logger
        logging.getLogger("ankimcp").setLevel(python_level)
        logger.info(f"Log level set to: {level}")

        return {}

    def _handle_tools_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools/list request."""
        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
            }
            for tool in AVAILABLE_TOOLS
        ]
        return {"tools": tools}

    def _handle_resources_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle resources/list request.

        Returns decks and note types as browsable resources.
        URI scheme:
        - anki://deck/{deck_name} - A deck containing notes
        - anki://note/{note_id} - A specific note
        """
        anki: Optional[AnkiInterface] = getattr(self.server, "anki_interface", None)
        if not anki:
            raise JSONRPCError(INTERNAL_ERROR, "Anki interface not initialized")

        resources = []

        # Add decks as resources
        decks = asyncio.run(anki.list_decks())
        for deck in decks:
            deck_name = deck["name"]
            # URL-encode the deck name for the URI
            encoded_name = deck_name.replace(" ", "%20")
            resources.append(
                {
                    "uri": f"anki://deck/{encoded_name}",
                    "name": deck_name,
                    "description": f"Anki deck with {deck['card_count']} cards",
                    "mimeType": "application/json",
                }
            )

        return {"resources": resources}

    def _handle_resources_read(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle resources/read request.

        Reads content from:
        - anki://deck/{deck_name} - Returns notes in the deck
        - anki://note/{note_id} - Returns note details
        """
        uri = params.get("uri")
        if not uri:
            raise JSONRPCError(INVALID_PARAMS, "Missing 'uri' parameter")

        anki: Optional[AnkiInterface] = getattr(self.server, "anki_interface", None)
        if not anki:
            raise JSONRPCError(INTERNAL_ERROR, "Anki interface not initialized")

        # Parse the URI
        if not uri.startswith("anki://"):
            raise JSONRPCError(INVALID_PARAMS, f"Invalid URI scheme: {uri}")

        path = uri[7:]  # Remove "anki://"

        if path.startswith("deck/"):
            # Read deck contents (list of notes)
            deck_name = path[5:].replace("%20", " ")  # URL decode
            return self._read_deck_resource(anki, deck_name, uri)
        elif path.startswith("note/"):
            # Read note details
            try:
                note_id = int(path[5:])
            except ValueError:
                raise JSONRPCError(INVALID_PARAMS, f"Invalid note ID: {path[5:]}")
            return self._read_note_resource(anki, note_id, uri)
        else:
            raise JSONRPCError(INVALID_PARAMS, f"Unknown resource path: {path}")

    def _read_deck_resource(
        self, anki: AnkiInterface, deck_name: str, uri: str
    ) -> Dict[str, Any]:
        """Read a deck resource - returns notes in the deck."""
        # Search for notes in this deck
        notes = asyncio.run(anki.search_notes(f'"deck:{deck_name}"', limit=100))

        content = {
            "deck_name": deck_name,
            "note_count": len(notes),
            "notes": notes,
        }

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(content, indent=2),
                }
            ]
        }

    def _read_note_resource(
        self, anki: AnkiInterface, note_id: int, uri: str
    ) -> Dict[str, Any]:
        """Read a note resource - returns note details."""
        note = asyncio.run(anki.get_note(note_id))
        cards = asyncio.run(anki.get_cards_for_note(note_id))

        content = {
            **note,
            "cards": cards,
        }

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(content, indent=2),
                }
            ]
        }

    def _handle_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools/call request."""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if not tool_name:
            raise JSONRPCError(INVALID_PARAMS, "Missing 'name' parameter")

        anki: Optional[AnkiInterface] = getattr(self.server, "anki_interface", None)
        if not anki:
            raise JSONRPCError(INTERNAL_ERROR, "Anki interface not initialized")

        executor = ToolExecutor(anki)
        results = _run_on_main_thread(
            lambda: asyncio.run(executor.execute(tool_name, arguments))
        )

        content = [{"type": "text", "text": r.text} for r in results]
        is_error = any(r.is_error for r in results)

        return {
            "content": content,
            "isError": is_error,
        }

    def _send_json_response(self, status_code: int, data: Any) -> None:
        """Send a JSON response."""
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _send_accepted(self) -> None:
        """Send HTTP 202 Accepted with no body (for notifications/responses)."""
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_method_not_allowed(self) -> None:
        """Send HTTP 405 for unsupported methods on /mcp."""
        self.send_response(405)
        self.send_header("Allow", "POST")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                JSONRPCHandler.error_response(
                    None, INVALID_REQUEST, "Method not allowed; use POST"
                )
            ).encode("utf-8")
        )

    def log_message(self, format: str, *args) -> None:
        """Override to use logger instead of stderr."""
        logger.debug(f"{self.address_string()} - {format % args}")


class ThreadedHTTPServer(HTTPServer):
    """HTTPServer that handles each request in a new thread."""

    daemon_threads = True
    allow_reuse_address = True

    def process_request(self, request, client_address):
        """Start a new thread to process the request."""
        thread = Thread(
            target=self.process_request_thread, args=(request, client_address)
        )
        thread.daemon = self.daemon_threads
        thread.start()

    def process_request_thread(self, request, client_address):
        """Process request in thread."""
        try:
            self.finish_request(request, client_address)
        except (ConnectionResetError, BrokenPipeError):
            # Client closed connection before response was sent - normal behavior
            pass
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


class SimpleHTTPServer:
    """MCP-compliant HTTP server for AnkiMCP (Streamable HTTP transport)."""

    def __init__(self, anki: AnkiInterface, host: str = "localhost", port: int = 4473):
        self.anki = anki
        self.host = host
        self.port = port
        self.server: Optional[ThreadedHTTPServer] = None
        self.thread: Optional[Thread] = None

    def start(self) -> None:
        """Start the HTTP server in a separate thread."""
        self.server = ThreadedHTTPServer((self.host, self.port), MCPRequestHandler)
        setattr(self.server, "anki_interface", self.anki)

        self.thread = Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

        base_url = f"http://{self.host}:{self.port}"
        logger.info("AnkiMCP server started:")
        logger.info(f"  MCP endpoint (Streamable HTTP): {base_url}/mcp")
        logger.info(f"  Health check: {base_url}/health")

    def stop(self) -> None:
        """Stop the HTTP server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            if self.thread:
                self.thread.join()
            logger.info("AnkiMCP server stopped")
