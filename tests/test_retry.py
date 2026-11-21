import unittest

from src.skills.retry import call_with_retry, error_status_code, is_transient_error


class FakeHttpError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class RetryTests(unittest.TestCase):
    def test_retries_transient_error_with_exponential_backoff(self):
        calls = 0
        delays = []

        def operation():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise FakeHttpError(429)
            return "ok"

        result = call_with_retry(
            operation,
            max_attempts=4,
            base_delay_seconds=2,
            sleep=delays.append,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(calls, 3)
        self.assertEqual(delays, [2, 4])

    def test_does_not_retry_permanent_client_error(self):
        calls = 0

        def operation():
            nonlocal calls
            calls += 1
            raise FakeHttpError(400)

        with self.assertRaises(FakeHttpError):
            call_with_retry(operation, sleep=lambda _: None)
        self.assertEqual(calls, 1)

    def test_reraises_last_error_after_attempts_are_exhausted(self):
        error = FakeHttpError(503)
        calls = 0

        def operation():
            nonlocal calls
            calls += 1
            raise error

        with self.assertRaises(FakeHttpError) as raised:
            call_with_retry(operation, max_attempts=3, sleep=lambda _: None)
        self.assertIs(raised.exception, error)
        self.assertEqual(calls, 3)

    def test_extracts_status_from_sdk_style_message(self):
        error = RuntimeError("429 RESOURCE_EXHAUSTED: riprovare")
        self.assertEqual(error_status_code(error), 429)
        self.assertTrue(is_transient_error(error))

    def test_follows_wrapped_error_cause(self):
        try:
            raise FakeHttpError(503)
        except FakeHttpError as cause:
            wrapped = RuntimeError("servizio esterno fallito")
            wrapped.__cause__ = cause

        self.assertEqual(error_status_code(wrapped), 503)
        self.assertTrue(is_transient_error(wrapped))


if __name__ == "__main__":
    unittest.main()
