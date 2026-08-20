# Presence of this file makes pytest add this directory (repo root) to its
# conftest-loading chain. It inserts `framework/` onto sys.path so
# `import orchestrator` resolves in tests without installing the package --
# the `orchestrator` package itself lives one level down, at
# framework/orchestrator/, not directly next to this file.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "framework"))
