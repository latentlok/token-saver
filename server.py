#!/usr/bin/env python3
"""executor MCP server -- entry point (the file .mcp.json runs).

At v2 cutover (M6) this shrank to a shim: all logic lives in the `qd/` package
(qd.server = threaded dispatch, qd.engine = the delegation loop, qd.gittree =
the trust machinery, ...). The v1 monolith that built v2 served as a frozen
differential oracle through the cutover and was deleted once the specs pinned
their own contracts; git history has it if a v1 behaviour is ever in question.
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
