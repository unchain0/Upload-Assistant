# Import-compatibility shim: implementation lives in the fork layout.
from src.integrations.observability.terminal_link_formatting import (  # noqa: F401
    format_terminal_link,
    parse_bool,
    should_embed_links,
)
