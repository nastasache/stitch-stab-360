import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import shutil
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.git_rag import (
    init_db,
    index_all,
    query_index,
    sanitize_fts_query,
    record_memory,
)


class TestGitRAG(unittest.TestCase):
    def setUp(self):
        self.temp_dir = REPO_ROOT / "tests" / "temp" / "unit_git_rag"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp_dir / ".agents" / ".git_rag.db"

    def test_sanitize_fts_query(self):
        """Test FTS5 query string sanitizer."""
        self.assertEqual(sanitize_fts_query(""), "")
        self.assertIn('"horizon"*', sanitize_fts_query("horizon stabilizer"))
        self.assertIn('"stabilizer"*', sanitize_fts_query("horizon stabilizer"))

    def test_record_and_query_memory(self):
        """Test recording markdown memory and querying it."""
        # Create memory directory structure
        mem_dir = self.temp_dir / ".agents" / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)

        # Record a test memory
        record_memory(
            self.temp_dir,
            "Equirectangular Trigonometry Fix",
            "Corrected horizon pitch/roll conversion matrices for equirectangular projection.",
            target="markdown",
            filename="decisions.md"
        )

        # Query index
        results = query_index(self.temp_dir, "equirectangular trigonometry")
        self.assertGreater(len(results), 0, "Expected at least 1 search result.")
        self.assertIn("Equirectangular Trigonometry Fix", results[0]["title"])
        self.assertIn("decisions.md", results[0]["ref"])


if __name__ == "__main__":
    unittest.main()
