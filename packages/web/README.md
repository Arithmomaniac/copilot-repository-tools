# Copilot Repository Tools - Web

Flask-based web interface for browsing and searching VS Code Copilot chat history.

## Quick Start

Run directly with uvx (no installation needed):

```bash
# First, scan for chat sessions
uvx copilot-repository-tools-cli scan

# Then start the web server
uvx copilot-repository-tools-web
```

Open `http://127.0.0.1:5000/` in your browser.

## Installation

```bash
uv tool install copilot-repository-tools-web
```

Or with pip:

```bash
pip install copilot-repository-tools-web
```

## Usage

```bash
copilot-chat-web                              # Start with defaults
copilot-chat-web --db my_chats.db             # Custom database
copilot-chat-web --port 8080                  # Custom port
copilot-chat-web --title "My Copilot Archive" # Custom title
```

## Features

- **Session list** with workspace names and message counts
- **Workspace filtering** to focus on specific projects
- **Full-text search** with highlighting
- **Dark mode support** via CSS `prefers-color-scheme`
- **Responsive design** for mobile and desktop
- **Syntax highlighting** for code blocks
- **Incremental refresh** to update without restarting

## Attribution

This package borrows patterns from [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) for the HTML transcript generation approach.
