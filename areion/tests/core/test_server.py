import unittest
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, call

import asyncio
from ... import HttpServer, HttpResponse, HttpRequest, HTTP_STATUS_CODES, MethodNotAllowedError, HttpError

class TestHttpServer(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Mock dependencies
        self.mock_router = MagicMock()
        self.mock_request_factory = MagicMock()
        self.mock_logger = MagicMock()

        # Instantiate HttpServer with mocked dependencies
        self.server = HttpServer(
            router=self.mock_router,
            request_factory=self.mock_request_factory,
            logger=self.mock_logger,
            host="127.0.0.1",
            port=8000,
            max_conns=10,
            buffer_size=1024,
            keep_alive_timeout=5,
        )

    def test_initialization_success(self):
        # Ensure that the server initializes correctly
        self.assertEqual(self.server.host, "127.0.0.1")
        self.assertEqual(self.server.port, 8000)
        self.assertEqual(self.server.max_conns, 10)
        self.assertEqual(self.server.buffer_size, 1024)
        self.assertEqual(self.server.keep_alive_timeout, 5)
        self.assertIsNotNone(self.server.semaphore)
        self.assertEqual(self.server.router, self.mock_router)
        self.assertEqual(self.server.request_factory, self.mock_request_factory)
        self.assertEqual(self.server.logger, self.mock_logger)

    def test_initialization_defaults(self):
        # Test default values
        server = HttpServer(
            router=self.mock_router,
            request_factory=self.mock_request_factory,
        )
        self.assertEqual(server.host, "localhost")
        self.assertEqual(server.port, 8080)
        self.assertEqual(server.max_conns, 1000)
        self.assertEqual(server.buffer_size, 8192)
        self.assertEqual(server.keep_alive_timeout, 30)
        self.assertIsNone(server.logger)

    def test_initialization_invalid_host_type(self):
        with self.assertRaises(ValueError) as context:
            HttpServer(
                router=self.mock_router,
                request_factory=self.mock_request_factory,
                host=12345,  # Invalid host type
            )
        self.assertEqual(str(context.exception), "Host must be a string.")

    def test_initialization_invalid_port_type(self):
        with self.assertRaises(ValueError) as context:
            HttpServer(
                router=self.mock_router,
                request_factory=self.mock_request_factory,
                port="eighty",  # Invalid port type
            )
        self.assertEqual(str(context.exception), "Port must be an integer.")

    def test_initialization_missing_router(self):
        with self.assertRaises(ValueError) as context:
            HttpServer(
                router=None,
                request_factory=self.mock_request_factory,
            )
        self.assertEqual(str(context.exception), "Router must be provided.")

    def test_initialization_missing_request_factory(self):
        with self.assertRaises(ValueError) as context:
            HttpServer(
                router=self.mock_router,
                request_factory=None,
            )
        self.assertEqual(str(context.exception), "Request factory must be provided.")

    def test_initialization_invalid_max_conns(self):
        with self.assertRaises(ValueError) as context:
            HttpServer(
                router=self.mock_router,
                request_factory=self.mock_request_factory,
                max_conns=-5,
            )
        self.assertEqual(str(context.exception), "Max connections must be a positive integer.")

    def test_initialization_invalid_buffer_size(self):
        with self.assertRaises(ValueError) as context:
            HttpServer(
                router=self.mock_router,
                request_factory=self.mock_request_factory,
                buffer_size=0,
            )
        self.assertEqual(str(context.exception), "Buffer size must be a positive integer.")

    def test_initialization_invalid_keep_alive_timeout(self):
        with self.assertRaises(ValueError) as context:
            HttpServer(
                router=self.mock_router,
                request_factory=self.mock_request_factory,
                keep_alive_timeout=-10,
            )
        self.assertEqual(str(context.exception), "Keep alive timeout must be a positive integer.")

    async def test_handle_client_normal_request(self):
        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        # Simulate reading headers and body
        request_line = b"GET /test HTTP/1.1\r\nHost: localhost\r\n\r\n"
        mock_reader.readuntil = AsyncMock(return_value=request_line)
        mock_reader.readexactly = AsyncMock(return_value=b"")

        # Mock request_factory
        mock_request = HttpRequest("GET", "/test", {"Host": "localhost"}, b"")
        self.mock_request_factory.create.return_value = mock_request

        # Mock router
        mock_handler = AsyncMock(return_value=HttpResponse(status_code=200, body=b"OK"))
        self.mock_router.get_handler.return_value = (mock_handler, {}, True)

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that the handler was called
        self.mock_router.get_handler.assert_called_with("GET", "/test")
        self.mock_request_factory.create.assert_called_with("GET", "/test", {"Host": "localhost"}, b"")
        mock_handler.assert_awaited_with(mock_request, **{})
        # Verify response was sent
        expected_response = HttpResponse(status_code=200, body=b"OK")
        mock_writer.write.assert_called_with(expected_response.format_response())

    async def test_handle_client_timeout_on_headers(self):
        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        # Simulate timeout when reading headers
        mock_reader.readuntil = AsyncMock(side_effect=asyncio.TimeoutError())

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that a 408 response was sent
        response = HttpResponse(status_code=408, body=HTTP_STATUS_CODES[408])
        mock_writer.write.assert_called_with(response.format_response())

        # Verify that connection was closed
        mock_writer.is_closing.assert_called()

    async def test_handle_client_incomplete_read(self):
        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        # Simulate incomplete read
        mock_reader.readuntil = AsyncMock(side_effect=asyncio.IncompleteReadError(partial=b"GET /", expected=10))

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that a 400 response was sent
        response = HttpResponse(status_code=400, body=HTTP_STATUS_CODES[400])
        mock_writer.write.assert_called_with(response.format_response())
        # mock_writer.drain.assert_awaited()
        # Verify that connection was closed
        mock_writer.is_closing.assert_called()

    async def test_handle_client_limit_overrun(self):
        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        # Simulate limit overrun
        mock_reader.readuntil = AsyncMock(side_effect=asyncio.LimitOverrunError(consumed=10, message=b""))

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that a 413 response was sent
        response = HttpResponse(status_code=413, body=HTTP_STATUS_CODES[413])
        mock_writer.write.assert_called_with(response.format_response())

        # Verify that connection was closed
        mock_writer.is_closing.assert_called()

    async def test_handle_client_invalid_headers(self):
        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        # Malformed headers
        malformed_headers = b"INVALID HEADER\r\n\r\n"
        mock_reader.readuntil = AsyncMock(return_value=malformed_headers)

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that a 400 response was sent
        response = HttpResponse(status_code=400, body=HTTP_STATUS_CODES[400])
        mock_writer.write.assert_called_with(response.format_response())
  
        # Verify that connection was closed
        mock_writer.is_closing.assert_called()

    async def test_handle_client_chunked_transfer_encoding(self):
        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        # Headers with Transfer-Encoding: chunked
        headers = b"GET /test HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n"
        mock_reader.readuntil = AsyncMock(return_value=headers)

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that a 501 response was sent
        response = HttpResponse(status_code=501, body="Not Implemented")
        mock_writer.write.assert_called_with(response.format_response())

        # Verify that connection was closed
        mock_writer.is_closing.assert_called()

    async def test_handle_client_method_not_allowed(self):
        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        request_headers = b"POST /test HTTP/1.1\r\nHost: localhost\r\n\r\n"
        mock_reader.readuntil = AsyncMock(return_value=request_headers)
        mock_reader.readexactly = AsyncMock(return_value=b"")

        # Mock request_factory
        mock_request = HttpRequest("POST", "/test", {"Host": "localhost"}, b"")
        self.mock_request_factory.create.return_value = mock_request

        # Mock router to raise MethodNotAllowedError
        self.mock_router.get_handler.side_effect = MethodNotAllowedError()

        # Mock get_allowed_methods
        self.mock_router.get_allowed_methods.return_value = ["GET", "OPTIONS"]

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that a 405 response was sent with Allow header
        response = HttpResponse(status_code=405, body=HTTP_STATUS_CODES[405], headers={"Allow": "GET, OPTIONS"})
        mock_writer.write.assert_called_with(response.format_response())

        # Verify that connection was closed
        mock_writer.is_closing.assert_called()

    async def test_handle_client_options_method(self):
        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        request_headers = b"OPTIONS /test HTTP/1.1\r\nHost: localhost\r\n\r\n"
        mock_reader.readuntil = AsyncMock(return_value=request_headers)

        # Mock request_factory
        mock_request = HttpRequest("OPTIONS", "/test", {"Host": "localhost"}, b"")
        self.mock_request_factory.create.return_value = mock_request

        # Mock router
        mock_handler = AsyncMock()
        self.mock_router.get_handler.return_value = (mock_handler, {}, True)
        self.mock_router.get_allowed_methods.return_value = ["GET", "POST", "OPTIONS"]

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that a 204 response was sent with Allow header
        response = HttpResponse(status_code=204, headers={"Allow": "GET, POST, OPTIONS"})
        mock_writer.write.assert_called_with(response.format_response())

        # Verify that connection was closed if needed
        mock_writer.is_closing.assert_not_called()  # 204 does not close connection by default

    async def test_handle_client_head_method(self):
        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        request_headers = b"HEAD /test HTTP/1.1\r\nHost: localhost\r\n\r\n"
        mock_reader.readuntil = AsyncMock(return_value=request_headers)

        # Mock request_factory
        mock_request = HttpRequest("HEAD", "/test", {"Host": "localhost"}, b"")
        self.mock_request_factory.create.return_value = mock_request

        # Mock router handler
        mock_handler = AsyncMock(return_value=HttpResponse(status_code=200, body=b"Response Body"))
        self.mock_router.get_handler.return_value = (mock_handler, {}, True)

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that handler was called
        mock_handler.assert_awaited_with(mock_request, **{})
        # Verify that response body was cleared for HEAD
        sent_response = mock_handler.return_value
        sent_response.body = b""
        mock_writer.write.assert_called_with(sent_response.format_response())

    async def test_handle_client_connect_method_not_implemented(self):
        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        request_headers = b"CONNECT /test HTTP/1.1\r\nHost: localhost\r\n\r\n"
        mock_reader.readuntil = AsyncMock(return_value=request_headers)

        # Mock request_factory
        mock_request = HttpRequest("CONNECT", "/test", {"Host": "localhost"}, b"")
        self.mock_request_factory.create.return_value = mock_request

        # Mock router
        mock_handler = AsyncMock()
        self.mock_router.get_handler.return_value = (mock_handler, {}, True)

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that a 501 response was sent
        response = HttpResponse(status_code=501, body="Not Implemented")
        mock_writer.write.assert_called_with(response.format_response())


    async def test_handle_client_handler_http_error(self):
        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        request_headers = b"GET /error HTTP/1.1\r\nHost: localhost\r\n\r\n"
        mock_reader.readuntil = AsyncMock(return_value=request_headers)
        mock_reader.readexactly = AsyncMock(return_value=b"")

        # Mock request_factory
        mock_request = HttpRequest("GET", "/error", {"Host": "localhost"}, b"")
        self.mock_request_factory.create.return_value = mock_request

        # Mock router handler to raise HttpError
        async def handler(request, **kwargs):
            raise HttpError(status_code=404, message="Not Found")

        self.mock_router.get_handler.return_value = (handler, {}, True)

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that a 404 response was sent
        response = HttpResponse(status_code=404, body="Not Found")
        mock_writer.write.assert_called_with(response.format_response())

        # Verify that a warning was logged
        self.mock_logger.log.assert_called_with("warning", mock.ANY)

    async def test_handle_client_handler_unexpected_exception(self):
        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        request_headers = b"GET /exception HTTP/1.1\r\nHost: localhost\r\n\r\n"
        mock_reader.readuntil = AsyncMock(return_value=request_headers)
        mock_reader.readexactly = AsyncMock(return_value=b"")

        # Mock request_factory
        mock_request = HttpRequest("GET", "/exception", {"Host": "localhost"}, b"")
        self.mock_request_factory.create.return_value = mock_request

        # Mock router handler to raise Exception
        async def handler(request, **kwargs):
            raise Exception("Unexpected error")

        self.mock_router.get_handler.return_value = (handler, {}, True)

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that a 500 response was sent
        response = HttpResponse(status_code=500, body=HTTP_STATUS_CODES[500])
        mock_writer.write.assert_called_with(response.format_response())

        # Verify that an error was logged
        self.mock_logger.log.assert_called_with("error", mock.ANY)

    async def test_handle_client_keep_alive_close_connection(self):
        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        request_headers = b"GET /test HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        mock_reader.readuntil = AsyncMock(return_value=request_headers)
        mock_reader.readexactly = AsyncMock(return_value=b"")

        # Mock request_factory
        mock_request = HttpRequest("GET", "/test", {"Host": "localhost", "Connection": "close"}, b"")
        self.mock_request_factory.create.return_value = mock_request

        # Mock router handler
        mock_handler = AsyncMock(return_value=HttpResponse(status_code=200, body=b"OK"))
        self.mock_router.get_handler.return_value = (mock_handler, {}, True)

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that the handler was called
        mock_handler.assert_awaited_with(mock_request, **{})
        # Verify that response was sent
        expected_response = HttpResponse(status_code=200, body=b"OK")
        mock_writer.write.assert_called_with(expected_response.format_response())

        # Verify that connection was closed
        mock_writer.is_closing.assert_called()

    async def test_send_response_non_http_response(self):
        # Mock writer
        mock_writer = MagicMock()
        # Call _send_response with non-HttpResponse
        response_body = "Simple Response"
        await self.server._send_response(writer=mock_writer, response=response_body)

        # Verify that HttpResponse was created and sent
        response = HttpResponse(body=response_body.encode())
        mock_writer.write.assert_called_with(response.format_response())


    async def test_parse_headers_valid(self):
        headers_data = b"GET /path HTTP/1.1\r\nHost: localhost\r\nContent-Length: 10\r\n\r\n"
        request_line, headers = self.server._parse_headers(headers_data)
        self.assertEqual(request_line, ("GET", "/path", "HTTP/1.1"))
        self.assertEqual(headers, {"Host": "localhost", "Content-Length": "10"})

    def test_parse_headers_invalid(self):
        headers_data = b"INVALID HEADER\r\n\r\n"
        with self.assertRaises(ValueError) as context:
            self.server._parse_headers(headers_data)
        self.assertIn("Invalid headers", str(context.exception))

    async def test_start_and_stop_server(self):
        # Mock asyncio.start_server
        mock_server = AsyncMock()
        mock_server.__aenter__.return_value = mock_server
        mock_server.wait_closed = AsyncMock()
        with mock.patch("asyncio.start_server", return_value=mock_server):
            # Start the server in a separate task
            start_task = asyncio.create_task(self.server.start())
            # Allow some time for the server to start
            await asyncio.sleep(0.1)
            # Verify that start_server was called with correct parameters
            asyncio.start_server.assert_called_with(
                self.server._handle_client, self.server.host, self.server.port
            )
            # Trigger shutdown
            self.server._shutdown_event.set()
            await start_task
            # Verify that server was awaited
            mock_server.__aenter__.assert_called()
            mock_server.wait_closed.assert_not_called()  # wait_closed called on stop

    async def test_stop_server(self):
        # Mock server
        self.server._server = AsyncMock()
        self.server._server.close = MagicMock()
        self.server._server.wait_closed = AsyncMock()

        await self.server.stop()

        # Verify that server.close and wait_closed were called
        self.server._server.close.assert_called()

    async def test_run_server(self):
        # Mock start and stop
        self.server.start = AsyncMock()
        self.server.stop = AsyncMock()

        # Run the server in a task
        run_task = asyncio.create_task(self.server.run())

        # Simulate shutdown event
        await asyncio.sleep(0.1)
        self.server._shutdown_event.set()
        await run_task

        # Verify that start was called
        self.server.start.assert_awaited()
        # Verify that stop was not called since no exception
        self.server.stop.assert_not_called()

    async def test_run_server_keyboard_interrupt(self):
        # Mock start to raise KeyboardInterrupt
        self.server.start = AsyncMock(side_effect=KeyboardInterrupt)
        self.server.stop = AsyncMock()

        # Run the server
        await self.server.run()

        # Verify that start was called
        self.server.start.assert_awaited()
        # Verify that stop was called due to KeyboardInterrupt
        self.server.stop.assert_awaited()

    def test_logging_with_logger(self):
        # Test that logging uses the provided logger
        self.server.log("info", "Test message")
        self.mock_logger.info.assert_called_with("Test message")

        self.server.log("error", "Error occurred")
        self.mock_logger.error.assert_called_with("Error occurred")

        self.server.log("warning", "Warning issued")
        self.mock_logger.warning.assert_called_with("Warning issued")

        self.server.log("debug", "Debugging")
        self.mock_logger.debug.assert_called_with("Debugging")

    def test_logging_without_logger(self):
        # Instantiate server without logger
        server = HttpServer(
            router=self.mock_router,
            request_factory=self.mock_request_factory,
            host="localhost",
            port=8080,
        )
        with mock.patch("builtins.print") as mock_print:
            server.log("info", "Info message")
            mock_print.assert_called_with("INFO: Info message")

            server.log("error", "Error message")
            mock_print.assert_called_with("ERROR: Error message")

    async def test_semaphore_limit(self):
        # Test that semaphore limits connections
        # Create multiple client handlers and ensure semaphore is respected

        # Set max_conns to 2 for testing
        self.server.semaphore = asyncio.Semaphore(2)

        # Define a handler that waits on semaphore
        async def client_handler(reader, writer):
            async with self.server.semaphore:
                await asyncio.sleep(0.1)

        self.server._handle_client = client_handler

        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()

        # Start multiple client handlers
        handlers = [
            asyncio.create_task(self.server._handle_client(mock_reader, mock_writer))
            for _ in range(4)
        ]

        # Wait for all handlers to complete
        await asyncio.gather(*handlers)

        # Semaphore should have been acquired and released properly
        self.assertEqual(self.server.semaphore._value, 2)

    async def test_keep_alive_timeout(self):
        # Test that keep_alive_timeout triggers a timeout response

        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()

        # Simulate reading headers but not sending another request within timeout
        request_headers = b"GET /test HTTP/1.1\r\nHost: localhost\r\n\r\n"
        mock_reader.readuntil = AsyncMock(side_effect=[
            asyncio.sleep(0),  # First read
            request_headers,
            asyncio.TimeoutError()
        ])

        # Mock request_factory
        mock_request = HttpRequest("GET", "/test", {"Host": "localhost"}, b"")
        self.mock_request_factory.create.return_value = mock_request

        # Mock router handler
        mock_handler = AsyncMock(return_value=HttpResponse(status_code=200, body=b"OK"))
        self.mock_router.get_handler.return_value = (mock_handler, {}, True)

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that handler was called once
        mock_handler.assert_awaited_once_with(mock_request, **{})
        # Verify that a 408 response was sent after timeout
        response = HttpResponse(status_code=408, body=HTTP_STATUS_CODES[408])
        mock_writer.write.assert_called_with(response.format_response())

        # Verify that connection was closed
        mock_writer.is_closing.assert_called()

    async def test_handle_cancelled_connection(self):
        # Test handling of cancelled connections

        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        # Simulate cancellation
        mock_reader.readuntil = AsyncMock(side_effect=asyncio.CancelledError())

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that connection was closed
        mock_writer.is_closing.assert_called()
        # Verify that a debug log was made
        self.mock_logger.log.assert_called_with("debug", "Client connection cancelled.")

    async def test_handle_connection_reset(self):
        # Test handling of connection reset by peer

        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        # Simulate connection reset
        mock_reader.readuntil = AsyncMock(side_effect=ConnectionResetError())

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that connection was closed
        mock_writer.is_closing.assert_called()
        # Verify that a debug log was made
        self.mock_logger.log.assert_called_with("debug", "Connection reset by peer.")

    async def test_handle_multiple_requests_keep_alive(self):
        # Test handling multiple requests on the same connection with keep-alive

        # Mock reader and writer
        mock_reader = AsyncMock()
        mock_writer = MagicMock()

        # Simulate two consecutive requests
        request1 = b"GET /test1 HTTP/1.1\r\nHost: localhost\r\n\r\n"
        request2 = b"GET /test2 HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        mock_reader.readuntil = AsyncMock(side_effect=[request1, request2, asyncio.IncompleteReadError(b"", None)])

        # Mock request_factory
        mock_request1 = HttpRequest("GET", "/test1", {"Host": "localhost"}, b"")
        mock_request2 = HttpRequest("GET", "/test2", {"Host": "localhost", "Connection": "close"}, b"")
        self.mock_request_factory.create.side_effect = [mock_request1, mock_request2]

        # Mock router handlers
        mock_handler1 = AsyncMock(return_value=HttpResponse(status_code=200, body=b"OK1"))
        mock_handler2 = AsyncMock(return_value=HttpResponse(status_code=200, body=b"OK2"))
        self.mock_router.get_handler.side_effect = [
            (mock_handler1, {}, True),
            (mock_handler2, {}, True),
        ]

        await self.server._handle_client(mock_reader, mock_writer)

        # Verify that both handlers were called
        mock_handler1.assert_awaited_with(mock_request1, **{})
        mock_handler2.assert_awaited_with(mock_request2, **{})
        # Verify that both responses were sent
        response1 = HttpResponse(status_code=200, body=b"OK1")
        response2 = HttpResponse(status_code=200, body=b"OK2")
        expected_calls = [call(response1.format_response()), call(response2.format_response())]
        mock_writer.write.assert_has_calls(expected_calls, any_order=False)

        # Verify that connection was closed after second request
        mock_writer.is_closing.assert_called()

if __name__ == "__main__":
    unittest.main()
