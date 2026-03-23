"""Tests for peanut_reacts.core.retry."""

from __future__ import annotations

import pytest

from peanut_reacts.core.retry import retry


class TestRetry:
    def test_succeeds_first_try(self):
        call_count = 0

        @retry(max_attempts=3, delay=0.01)
        def func():
            nonlocal call_count
            call_count += 1
            return "ok"

        assert func() == "ok"
        assert call_count == 1

    def test_retries_on_failure(self):
        call_count = 0

        @retry(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        def func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("transient")
            return "ok"

        assert func() == "ok"
        assert call_count == 3

    def test_raises_after_max_attempts(self):
        @retry(max_attempts=2, delay=0.01, exceptions=(RuntimeError,))
        def func():
            raise RuntimeError("permanent")

        with pytest.raises(RuntimeError, match="permanent"):
            func()

    def test_does_not_catch_wrong_exception(self):
        @retry(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        def func():
            raise TypeError("wrong type")

        with pytest.raises(TypeError):
            func()

    def test_backoff_multiplier(self):
        """Verify that more attempts happen (backoff doesn't prevent them)."""
        call_count = 0

        @retry(max_attempts=4, delay=0.01, backoff=1.5, exceptions=(OSError,))
        def func():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise OSError("fail")
            return "done"

        assert func() == "done"
        assert call_count == 4
