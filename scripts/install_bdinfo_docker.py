#!/usr/bin/env python3
"""Docker build composition root for the bdinfo runtime-tool installer."""

from src.integrations.runtime_tools.bdinfo_docker import main

if __name__ == "__main__":
    raise SystemExit(main())
