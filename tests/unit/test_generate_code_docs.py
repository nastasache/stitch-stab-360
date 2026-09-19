import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import ast
import unittest
from pathlib import Path

# Add repo root to path
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from scripts.generate_code_docs import (
    extract_function_signature,
    format_docstring,
    parse_module,
    generate_markdown,
)


class TestGenerateCodeDocs(unittest.TestCase):
    """Unit tests for the AST-based code documentation generator."""

    def test_extract_function_signature_sync(self):
        """Should correctly extract signature for standard synchronous functions."""
        code = "def sample_func(a: int, b: str = 'default') -> bool:\n    pass"
        fn_node = ast.parse(code).body[0]
        sig = extract_function_signature(fn_node)
        self.assertIn("def sample_func(a: int, b: str='default') -> bool", sig)

    def test_extract_function_signature_async(self):
        """Should correctly extract signature for asynchronous coroutines."""
        code = "async def async_handler(endpoint: str, timeout: float = 5.0) -> dict:\n    pass"
        fn_node = ast.parse(code).body[0]
        sig = extract_function_signature(fn_node)
        self.assertTrue(sig.startswith("async def async_handler"))
        self.assertIn("endpoint: str", sig)
        self.assertIn("-> dict", sig)

    def test_format_docstring_google_style(self):
        """Should format Google-style Args and Returns sections cleanly."""
        raw_doc = (
            "Short summary.\n\n"
            "Args:\n"
            "    param1: First parameter description.\n"
            "    param2: Second parameter description.\n\n"
            "Returns:\n"
            "    True on success.\n"
        )
        formatted = format_docstring(raw_doc)
        self.assertIn("**Args:**", formatted)
        self.assertIn("- `param1`: First parameter description.", formatted)
        self.assertIn("**Returns:**", formatted)
        self.assertIn("True on success.", formatted)

    def test_parse_module_and_generate_markdown(self):
        """Should parse module AST and produce valid Markdown content."""
        target_file = os.path.join(_repo_root, "scripts", "generate_code_docs.py")
        mod_info = parse_module(target_file)
        self.assertTrue(mod_info["file"].replace("\\", "/").endswith("scripts/generate_code_docs.py"))
        self.assertTrue(len(mod_info["functions"]) > 0)

        # Generate markdown slice
        categories = [("Developer Tools", "Test description", [mod_info])]
        md = generate_markdown(categories)
        self.assertIn("# 📖 Code Reference & API Documentation", md)
        self.assertIn("extract_function_signature", md)


if __name__ == "__main__":
    unittest.main()
