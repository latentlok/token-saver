#!/usr/bin/env python3
"""qwen-delegate MCP server -- entry point (the file .mcp.json runs).

At v2 cutover (M6) this shrank to a shim: all logic lives in the `qd/` package
(qd.server = threaded dispatch, qd.engine = the delegation loop, qd.gittree =
the trust machinery, ...). The v1 monolith that built v2 was demoted to
_ref_impl.py, a frozen differential oracle used only by the crane-equality
specs -- it is never on the runtime path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qd.server import main  # noqa: E402

if __name__ == "__main__":
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
