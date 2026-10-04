"""First-party offline regression tests."""

import os
import tempfile

# api.logger registers its rotating file sink at import time. Redirect the
# data directory (for this process and any subprocess it spawns) before any
# api module is imported, so test runs never create ./chaoxing.log in the
# repository root.
os.environ.setdefault("CHAOXING_DATA_DIR", tempfile.mkdtemp(prefix="chaoxing-test-data-"))
