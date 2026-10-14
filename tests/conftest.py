from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# The saved HTML pages are gitignored (they're the sites' content), so tests
# that parse them only run where the fixtures exist locally.
needs_fixtures = pytest.mark.skipif(
    not any(FIXTURES.glob("*.html")),
    reason="tests/fixtures/*.html not present (gitignored; save listing pages there to run)",
)
