"""Web viewer module for displaying Copilot chat archive."""

import html
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from .database import Database


def _markdown_to_html(text: str) -> str:
    """Simple markdown-like conversion to HTML.

    Handles:
    - Code blocks (```)
    - Inline code (`)
    - Line breaks
    """
    if not text:
        return ""

    result = []
    lines = text.split("\n")
    in_code_block = False
    code_block_content = []
    code_language = ""

    for line in lines:
        if line.startswith("```"):
            if in_code_block:
                # End code block
                code = html.escape("\n".join(code_block_content))
                result.append(f'<pre><code class="language-{code_language}">{code}</code></pre>')
                code_block_content = []
                in_code_block = False
            else:
                # Start code block
                in_code_block = True
                code_language = line[3:].strip() or "text"
        elif in_code_block:
            code_block_content.append(line)
        else:
            # Handle inline code
            processed = ""
            i = 0
            while i < len(line):
                if line[i] == "`":
                    # Find closing backtick
                    end = line.find("`", i + 1)
                    if end != -1:
                        code = html.escape(line[i + 1 : end])
                        processed += f"<code>{code}</code>"
                        i = end + 1
                    else:
                        processed += html.escape(line[i])
                        i += 1
                else:
                    processed += html.escape(line[i])
                    i += 1
            result.append(processed)

    # Handle unclosed code block
    if in_code_block and code_block_content:
        code = html.escape("\n".join(code_block_content))
        result.append(f'<pre><code class="language-{code_language}">{code}</code></pre>')

    return "<br>\n".join(result)


def get_jinja_env() -> Environment:
    """Get a configured Jinja2 environment."""
    env = Environment(
        loader=PackageLoader("copilot_chat_archive", "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["markdown"] = _markdown_to_html
    return env


def generate_html(
    db: Database,
    output_dir: str | Path,
    title: str = "Copilot Chat Archive",
) -> Path:
    """Generate static HTML files for the chat archive.

    Args:
        db: Database instance to read from.
        output_dir: Directory to write HTML files to.
        title: Title for the archive.

    Returns:
        Path to the generated index.html.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    env = get_jinja_env()

    # Generate index page
    sessions = db.list_sessions()
    workspaces = db.get_workspaces()
    stats = db.get_stats()

    index_template = env.get_template("index.html")
    index_html = index_template.render(
        title=title,
        sessions=sessions,
        workspaces=workspaces,
        stats=stats,
    )
    index_path = output_path / "index.html"
    index_path.write_text(index_html, encoding="utf-8")

    # Generate individual session pages
    sessions_dir = output_path / "sessions"
    sessions_dir.mkdir(exist_ok=True)

    session_template = env.get_template("session.html")
    for session_info in sessions:
        session = db.get_session(session_info["session_id"])
        if session:
            session_html = session_template.render(
                title=title,
                session=session,
                message_count=len(session.messages),
            )
            session_file = sessions_dir / f"{session.session_id}.html"
            session_file.write_text(session_html, encoding="utf-8")

    # Copy static assets
    _write_static_assets(output_path)

    return index_path


def _write_static_assets(output_dir: Path):
    """Write static CSS and JS files."""
    static_dir = output_dir / "static"
    static_dir.mkdir(exist_ok=True)

    # Write CSS
    css_content = """
:root {
    --bg-primary: #ffffff;
    --bg-secondary: #f6f8fa;
    --bg-tertiary: #f0f0f0;
    --text-primary: #24292f;
    --text-secondary: #57606a;
    --border-color: #d0d7de;
    --link-color: #0969da;
    --user-bg: #ddf4ff;
    --assistant-bg: #ffffff;
    --code-bg: #f6f8fa;
    --mark-bg: #fff8c5;
}

@media (prefers-color-scheme: dark) {
    :root {
        --bg-primary: #0d1117;
        --bg-secondary: #161b22;
        --bg-tertiary: #21262d;
        --text-primary: #c9d1d9;
        --text-secondary: #8b949e;
        --border-color: #30363d;
        --link-color: #58a6ff;
        --user-bg: #1f3d5c;
        --assistant-bg: #161b22;
        --code-bg: #161b22;
        --mark-bg: #3d3d00;
    }
}

* {
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
    line-height: 1.6;
    color: var(--text-primary);
    background-color: var(--bg-primary);
    margin: 0;
    padding: 0;
}

.container {
    max-width: 1200px;
    margin: 0 auto;
    padding: 20px;
}

header {
    background-color: var(--bg-secondary);
    border-bottom: 1px solid var(--border-color);
    padding: 20px 0;
    margin-bottom: 20px;
}

header h1 {
    margin: 0;
}

header .stats {
    color: var(--text-secondary);
    font-size: 0.9em;
    margin-top: 10px;
}

.search-box {
    margin: 20px 0;
}

.search-box input {
    width: 100%;
    padding: 12px 16px;
    font-size: 16px;
    border: 1px solid var(--border-color);
    border-radius: 6px;
    background-color: var(--bg-primary);
    color: var(--text-primary);
}

.search-box input:focus {
    outline: none;
    border-color: var(--link-color);
    box-shadow: 0 0 0 3px rgba(9, 105, 218, 0.3);
}

.workspaces {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 20px;
}

.workspace-tag {
    background-color: var(--bg-tertiary);
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.85em;
    color: var(--text-secondary);
    cursor: pointer;
    transition: background-color 0.2s;
}

.workspace-tag:hover {
    background-color: var(--border-color);
}

.workspace-tag.active {
    background-color: var(--link-color);
    color: white;
}

.session-list {
    list-style: none;
    padding: 0;
    margin: 0;
}

.session-item {
    border: 1px solid var(--border-color);
    border-radius: 6px;
    margin-bottom: 10px;
    padding: 16px;
    background-color: var(--bg-secondary);
    transition: box-shadow 0.2s;
}

.session-item:hover {
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.session-item a {
    color: var(--link-color);
    text-decoration: none;
    font-weight: 500;
}

.session-item a:hover {
    text-decoration: underline;
}

.session-meta {
    color: var(--text-secondary);
    font-size: 0.85em;
    margin-top: 8px;
}

.session-meta span {
    margin-right: 16px;
}

.message {
    padding: 16px;
    margin-bottom: 16px;
    border-radius: 8px;
    border: 1px solid var(--border-color);
}

.message.user {
    background-color: var(--user-bg);
}

.message.assistant {
    background-color: var(--assistant-bg);
}

.message-role {
    font-weight: 600;
    font-size: 0.85em;
    text-transform: uppercase;
    margin-bottom: 8px;
    color: var(--text-secondary);
}

.message-content {
    word-wrap: break-word;
    overflow-wrap: break-word;
}

.message-content pre {
    background-color: var(--code-bg);
    padding: 16px;
    border-radius: 6px;
    overflow-x: auto;
    border: 1px solid var(--border-color);
}

.message-content code {
    font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace;
    font-size: 0.9em;
    background-color: var(--code-bg);
    padding: 2px 6px;
    border-radius: 3px;
}

.message-content pre code {
    background-color: transparent;
    padding: 0;
}

mark {
    background-color: var(--mark-bg);
    padding: 2px 4px;
    border-radius: 2px;
}

.back-link {
    display: inline-block;
    margin-bottom: 20px;
    color: var(--link-color);
    text-decoration: none;
}

.back-link:hover {
    text-decoration: underline;
}

.edition-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 0.75em;
    font-weight: 500;
}

.edition-badge.stable {
    background-color: #dafbe1;
    color: #1a7f37;
}

.edition-badge.insider {
    background-color: #ddf4ff;
    color: #0969da;
}

@media (prefers-color-scheme: dark) {
    .edition-badge.stable {
        background-color: #238636;
        color: #dafbe1;
    }
    .edition-badge.insider {
        background-color: #1f6feb;
        color: #ddf4ff;
    }
}

.no-results {
    text-align: center;
    color: var(--text-secondary);
    padding: 40px;
}

footer {
    margin-top: 40px;
    padding: 20px 0;
    border-top: 1px solid var(--border-color);
    color: var(--text-secondary);
    font-size: 0.85em;
    text-align: center;
}

/* Message header with anchor link */
.message-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
}

.message-anchor {
    color: var(--text-secondary);
    text-decoration: none;
    font-size: 0.8em;
    opacity: 0.5;
    transition: opacity 0.2s;
}

.message-anchor:hover {
    opacity: 1;
    color: var(--link-color);
}

.message.highlighted {
    animation: highlight-pulse 2s ease-out;
}

@keyframes highlight-pulse {
    0% { box-shadow: 0 0 0 4px var(--link-color); }
    100% { box-shadow: none; }
}

/* Session ID display */
.session-id {
    margin-top: 10px;
    color: var(--text-secondary);
}

.session-id code {
    font-size: 0.85em;
    background-color: var(--code-bg);
    padding: 2px 6px;
    border-radius: 3px;
}

/* Collapsible sections for tool invocations, file changes, etc. */
.collapsible {
    margin-top: 12px;
    border: 1px solid var(--border-color);
    border-radius: 6px;
    background-color: var(--bg-tertiary);
}

.collapsible summary {
    padding: 10px 14px;
    cursor: pointer;
    font-weight: 500;
    font-size: 0.9em;
    color: var(--text-secondary);
    display: flex;
    align-items: center;
    gap: 8px;
    user-select: none;
}

.collapsible summary:hover {
    background-color: var(--border-color);
}

.collapsible-icon {
    font-size: 0.7em;
    transition: transform 0.2s;
}

.collapsible-content {
    padding: 12px 14px;
    border-top: 1px solid var(--border-color);
}

/* Tool invocations */
.tool-invocation {
    margin-bottom: 16px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border-color);
}

.tool-invocation:last-child {
    margin-bottom: 0;
    padding-bottom: 0;
    border-bottom: none;
}

.tool-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
}

.tool-section {
    margin-top: 8px;
}

.tool-label {
    display: block;
    font-size: 0.8em;
    color: var(--text-secondary);
    margin-bottom: 4px;
}

.tool-section pre {
    margin: 0;
    padding: 10px;
    font-size: 0.85em;
    max-height: 200px;
    overflow: auto;
}

/* Status badges */
.status-badge {
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 0.75em;
    font-weight: 500;
}

.status-badge.success {
    background-color: #dafbe1;
    color: #1a7f37;
}

.status-badge.error, .status-badge.failed {
    background-color: #ffebe9;
    color: #cf222e;
}

.status-badge.pending, .status-badge.running {
    background-color: #fff8c5;
    color: #9a6700;
}

@media (prefers-color-scheme: dark) {
    .status-badge.success {
        background-color: #238636;
        color: #dafbe1;
    }
    .status-badge.error, .status-badge.failed {
        background-color: #da3633;
        color: #ffebe9;
    }
    .status-badge.pending, .status-badge.running {
        background-color: #9e6a03;
        color: #fff8c5;
    }
}

/* File changes */
.file-change {
    margin-bottom: 16px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border-color);
}

.file-change:last-child {
    margin-bottom: 0;
    padding-bottom: 0;
    border-bottom: none;
}

.file-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
}

.language-badge {
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 0.75em;
    background-color: var(--bg-secondary);
    color: var(--text-secondary);
}

.file-explanation {
    color: var(--text-secondary);
    font-size: 0.9em;
    margin-bottom: 8px;
}

pre.diff {
    margin: 0;
    padding: 10px;
    font-size: 0.85em;
    max-height: 300px;
    overflow: auto;
}

/* Command runs */
.command-run {
    margin-bottom: 16px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border-color);
}

.command-run:last-child {
    margin-bottom: 0;
    padding-bottom: 0;
    border-bottom: none;
}

.command-header {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 8px;
}

.command-text {
    font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace;
    background-color: var(--code-bg);
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 0.9em;
}

pre.command-output {
    margin: 0;
    padding: 10px;
    font-size: 0.85em;
    max-height: 200px;
    overflow: auto;
}
"""
    (static_dir / "style.css").write_text(css_content.strip(), encoding="utf-8")

    # Write JavaScript for search functionality
    js_content = """
document.addEventListener('DOMContentLoaded', function() {
    const searchInput = document.getElementById('search-input');
    const sessionList = document.getElementById('session-list');
    const workspaceTags = document.querySelectorAll('.workspace-tag');
    
    if (searchInput && sessionList) {
        let sessions = Array.from(sessionList.querySelectorAll('.session-item'));
        let activeWorkspace = null;
        
        // Filter by workspace
        workspaceTags.forEach(tag => {
            tag.addEventListener('click', function() {
                const workspace = this.dataset.workspace;
                
                if (activeWorkspace === workspace) {
                    // Deselect
                    activeWorkspace = null;
                    this.classList.remove('active');
                } else {
                    // Select new workspace
                    workspaceTags.forEach(t => t.classList.remove('active'));
                    this.classList.add('active');
                    activeWorkspace = workspace;
                }
                
                filterSessions();
            });
        });
        
        // Filter by search text
        searchInput.addEventListener('input', filterSessions);
        
        function filterSessions() {
            const searchTerm = searchInput.value.toLowerCase();
            
            sessions.forEach(session => {
                const text = session.textContent.toLowerCase();
                const workspace = session.dataset.workspace;
                
                const matchesSearch = !searchTerm || text.includes(searchTerm);
                const matchesWorkspace = !activeWorkspace || workspace === activeWorkspace;
                
                session.style.display = matchesSearch && matchesWorkspace ? '' : 'none';
            });
            
            // Show "no results" message if needed
            const visible = sessions.filter(s => s.style.display !== 'none');
            let noResults = sessionList.querySelector('.no-results');
            
            if (visible.length === 0) {
                if (!noResults) {
                    noResults = document.createElement('li');
                    noResults.className = 'no-results';
                    noResults.textContent = 'No sessions found matching your criteria.';
                    sessionList.appendChild(noResults);
                }
            } else if (noResults) {
                noResults.remove();
            }
        }
    }
});
"""
    (static_dir / "script.js").write_text(js_content.strip(), encoding="utf-8")
