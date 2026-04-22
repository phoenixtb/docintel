"""
Tests for _run_db helper and asyncio migration (T13, T14).

_run_db must:
  1. Run the callable in a thread executor (freeing the event loop).
  2. Preserve contextvars (specifically _tenant_ctx and _role_ctx used for PG RLS).

asyncio migration (T14):
  3. RAGService.query and _maybe_compress_history use get_running_loop(), not get_event_loop().
"""

import asyncio
import contextvars
import pytest


# ---------------------------------------------------------------------------
# _run_db — contextvar preservation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.unit
class TestRunDbContextvarPreservation:

    async def test_tenant_ctx_visible_inside_executor(self):
        """_run_db preserves _tenant_ctx into the executor thread."""
        from src.context import _tenant_ctx

        _tenant_ctx.set("tenant-42")

        captured = {}

        def worker():
            captured["tenant"] = _tenant_ctx.get(None)

        # Import _run_db directly (it lives in main.py but is a pure function)
        from src.api.main import _run_db

        await _run_db(worker)
        assert captured["tenant"] == "tenant-42"

    async def test_role_ctx_visible_inside_executor(self):
        """_run_db preserves _role_ctx into the executor thread."""
        from src.context import _role_ctx

        _role_ctx.set("editor")

        captured = {}

        def worker():
            captured["role"] = _role_ctx.get(None)

        from src.api.main import _run_db

        await _run_db(worker)
        assert captured["role"] == "editor"

    async def test_return_value_propagated(self):
        """_run_db returns the callable's return value."""
        from src.api.main import _run_db

        result = await _run_db(lambda: 42)
        assert result == 42

    async def test_exception_propagated(self):
        """_run_db re-raises exceptions from the executor thread."""
        from src.api.main import _run_db

        with pytest.raises(ValueError, match="executor error"):
            await _run_db(lambda: (_ for _ in ()).throw(ValueError("executor error")))

    async def test_caller_context_isolation(self):
        """Changes to contextvar inside the worker do not leak back to caller."""
        from src.context import _tenant_ctx
        from src.api.main import _run_db

        _tenant_ctx.set("outer")

        def worker():
            _tenant_ctx.set("inner")

        await _run_db(worker)
        # Caller's contextvar is unchanged
        assert _tenant_ctx.get(None) == "outer"


# ---------------------------------------------------------------------------
# asyncio migration (T14): get_running_loop usage
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestAsyncioGetRunningLoopMigration:
    """get_event_loop() is deprecated in Python 3.10+; confirm migration to get_running_loop."""

    def test_query_pipeline_uses_get_running_loop(self):
        """pipelines/query.py must not contain get_event_loop() calls."""
        import ast
        from pathlib import Path

        src_path = Path(__file__).parent.parent / "src" / "pipelines" / "query.py"
        source = src_path.read_text()
        tree = ast.parse(source)

        violations = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "get_event_loop"
            ):
                violations.append(f"line {node.lineno}: asyncio.get_event_loop()")

        assert not violations, (
            "Deprecated asyncio.get_event_loop() found in query.py. "
            "Use asyncio.get_running_loop() instead.\n" + "\n".join(violations)
        )

    def test_main_prewarm_uses_get_running_loop(self):
        """api/main.py _prewarm_rag_service must not use get_event_loop()."""
        import ast
        from pathlib import Path

        src_path = Path(__file__).parent.parent / "src" / "api" / "main.py"
        source = src_path.read_text()
        tree = ast.parse(source)

        violations = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "get_event_loop"
            ):
                violations.append(f"line {node.lineno}: asyncio.get_event_loop()")

        assert not violations, (
            "Deprecated asyncio.get_event_loop() found in main.py.\n" + "\n".join(violations)
        )
