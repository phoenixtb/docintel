"""
Regression test for lazy package init (see docintel_common/__init__.py).

analytics-service-py (A7) depends on docintel-common only for
RedisStreamBus — it must not be forced to install/import torch,
transformers, or psycopg2 just to get the messaging submodule. Run in a
subprocess so we observe a clean interpreter's sys.modules, independent of
whatever earlier tests in this same pytest session may have imported.
"""

import subprocess
import sys


def test_importing_messaging_does_not_load_heavy_submodules():
    script = (
        "import sys\n"
        "from docintel_common.messaging import RedisStreamBus, TOPIC_ANALYTICS_QUERY\n"
        "heavy = [m for m in ('torch', 'transformers', 'psycopg2') if m in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    loaded_heavy_modules = result.stdout.strip()
    assert loaded_heavy_modules == "", (
        f"Importing docintel_common.messaging pulled in heavy modules: {loaded_heavy_modules}"
    )


def test_top_level_lazy_attr_still_works():
    """from docintel_common import RedisStreamBus (PEP 562 __getattr__) still resolves."""
    from docintel_common import RedisStreamBus, TOPIC_DOCUMENTS_READY

    assert RedisStreamBus.__name__ == "RedisStreamBus"
    assert TOPIC_DOCUMENTS_READY == "documents.ready"


def test_unknown_top_level_attr_raises_attribute_error():
    import docintel_common

    try:
        docintel_common.this_does_not_exist
    except AttributeError:
        pass
    else:
        raise AssertionError("Expected AttributeError for unknown attribute")
