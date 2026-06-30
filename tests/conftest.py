import os
import inspect
import pytest


def _patch_httpx_testclient_compatibility() -> None:
    try:
        import httpx
    except Exception:
        return

    if "app" in inspect.signature(httpx.Client.__init__).parameters:
        return

    original_init = httpx.Client.__init__

    def compatible_init(self, *args, **kwargs):
        kwargs.pop("app", None)
        return original_init(self, *args, **kwargs)

    httpx.Client.__init__ = compatible_init


_patch_httpx_testclient_compatibility()

@pytest.fixture(scope="session", autouse=True)
def clean_test_environment():
    # Force empty credentials for the test run to isolate from any local .env file
    # This prevents InboundAgent from loading them and triggering live API calls
    os.environ["BOOKING_API_KEY"] = ""
    os.environ["BOOKING_BASE_URL"] = ""
