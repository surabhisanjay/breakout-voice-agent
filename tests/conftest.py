import os
import pytest

@pytest.fixture(scope="session", autouse=True)
def clean_test_environment():
    # Force empty credentials for the test run to isolate from any local .env file
    # This prevents InboundAgent from loading them and triggering live API calls
    os.environ["BOOKING_API_KEY"] = ""
    os.environ["BOOKING_BASE_URL"] = ""
