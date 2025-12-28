"""Copilot Chat Archive - Create a searchable archive of VS Code GitHub Copilot chat history."""

__version__ = "0.1.0"

from .scanner import find_copilot_chat_dirs, scan_chat_sessions
from .database import Database
from .viewer import generate_html

__all__ = [
    "__version__",
    "find_copilot_chat_dirs",
    "scan_chat_sessions",
    "Database",
    "generate_html",
]
