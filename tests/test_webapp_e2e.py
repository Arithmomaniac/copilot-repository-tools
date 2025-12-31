"""Playwright end-to-end tests for the webapp."""

import tempfile
import threading
import time
from pathlib import Path

import pytest

from copilot_chat_archive.database import Database
from copilot_chat_archive.scanner import ChatMessage, ChatSession
from copilot_chat_archive.webapp import create_app


@pytest.fixture(scope="module")
def test_db():
    """Create a temporary database with sample data for e2e tests."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = Database(db_path)

    # Add session with custom title
    session1 = ChatSession(
        session_id="e2e-session-1",
        workspace_name="my-workspace",
        workspace_path="/home/user/my-workspace",
        custom_title="VS Code debug configuration for command line app",
        messages=[
            ChatMessage(role="user", content="How do I debug a command line app in VS Code?"),
            ChatMessage(
                role="assistant",
                content="To debug a command line app in VS Code, you need to:\n\n1. Create a launch.json file\n2. Configure the program path\n3. Set breakpoints\n\n```json\n{\n  \"type\": \"python\",\n  \"request\": \"launch\"\n}\n```",
            ),
            ChatMessage(role="user", content="What about Flask apps?"),
            ChatMessage(
                role="assistant",
                content="For Flask apps, you can use the following configuration:\n\n```json\n{\n  \"type\": \"python\",\n  \"module\": \"flask\"\n}\n```",
            ),
        ],
        created_at="1737021600000",
        vscode_edition="stable",
    )
    db.add_session(session1)

    # Add session without custom title
    session2 = ChatSession(
        session_id="e2e-session-2",
        workspace_name="another-project",
        workspace_path="/home/user/another",
        messages=[
            ChatMessage(role="user", content="What is Python?"),
            ChatMessage(
                role="assistant",
                content="Python is a high-level, interpreted programming language known for its simplicity and readability.",
            ),
        ],
        created_at="1737108000000",
        vscode_edition="insider",
    )
    db.add_session(session2)

    yield db_path

    Path(db_path).unlink(missing_ok=True)


@pytest.fixture(scope="module")
def live_server(test_db):
    """Start a live Flask server for Playwright tests."""
    app = create_app(test_db, title="E2E Test Archive")
    app.config["TESTING"] = True
    
    # Run server in a thread
    server_thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=5099, use_reloader=False, threaded=True)
    )
    server_thread.daemon = True
    server_thread.start()
    
    # Wait for server to start
    time.sleep(1)
    
    yield "http://127.0.0.1:5099"


class TestIndexPage:
    """Playwright tests for the index page."""

    def test_index_page_loads(self, live_server, page):
        """Test that the index page loads correctly."""
        page.goto(live_server)
        
        # Check title
        assert "E2E Test Archive" in page.title()
        
        # Check header
        assert page.locator("h1").text_content() == "E2E Test Archive"

    def test_sessions_displayed_with_custom_title(self, live_server, page):
        """Test that sessions with custom titles show the title as link."""
        page.goto(live_server)
        
        # Check that custom title is displayed as link
        link = page.locator("a:has-text('VS Code debug configuration')")
        assert link.is_visible()
        
        # Check workspace is shown as property
        workspace = page.locator(".session-workspace:has-text('my-workspace')")
        assert workspace.is_visible()

    def test_sessions_without_custom_title_show_workspace(self, live_server, page):
        """Test that sessions without custom title show workspace name as link."""
        page.goto(live_server)
        
        # Check that workspace name is displayed as link
        link = page.locator("a:has-text('another-project')")
        assert link.is_visible()

    def test_search_functionality(self, live_server, page):
        """Test that search returns results with snippets."""
        page.goto(f"{live_server}/?q=Flask")
        
        # Check search info is displayed
        assert page.locator(".search-info").is_visible()
        assert "Flask" in page.locator(".search-info").text_content()
        
        # Check that matching session is shown
        assert page.locator("a:has-text('VS Code debug')").is_visible()

    def test_search_snippets_with_message_links(self, live_server, page):
        """Test that search shows snippets with direct message links."""
        page.goto(f"{live_server}/?q=Flask")
        
        # Check snippets are displayed
        snippets = page.locator(".search-snippets")
        if snippets.count() > 0:
            # Check that snippet has a link to message
            snippet_links = snippets.first.locator("a")
            assert snippet_links.count() > 0
            href = snippet_links.first.get_attribute("href")
            assert "#msg-" in href

    def test_clear_search(self, live_server, page):
        """Test clearing search results."""
        page.goto(f"{live_server}/?q=Python")
        
        # Click clear search
        page.locator("a:has-text('clear search')").click()
        
        # Should be on index without query
        assert page.url == f"{live_server}/"
        
        # Both sessions should be visible
        assert page.locator("a:has-text('VS Code debug')").is_visible()
        assert page.locator("a:has-text('another-project')").is_visible()


class TestSessionPage:
    """Playwright tests for the session page."""

    def test_session_page_loads(self, live_server, page):
        """Test that a session page loads correctly."""
        page.goto(f"{live_server}/session/e2e-session-1")
        
        # Check title contains session info
        assert "VS Code debug" in page.title() or "my-workspace" in page.title()
        
        # Check back link exists
        assert page.locator("a:has-text('Back to all sessions')").is_visible()

    def test_session_shows_messages(self, live_server, page):
        """Test that session page shows all messages."""
        page.goto(f"{live_server}/session/e2e-session-1")
        
        # Check user message
        assert page.locator("text=How do I debug a command line app").is_visible()
        
        # Check assistant response
        assert page.locator("text=Create a launch.json file").is_visible()

    def test_message_anchor_links(self, live_server, page):
        """Test that message anchor links work."""
        page.goto(f"{live_server}/session/e2e-session-1#msg-3")
        
        # Message 3 should be in view (Flask question)
        flask_message = page.locator("#msg-3")
        assert flask_message.is_visible()

    def test_back_link_navigation(self, live_server, page):
        """Test that back link navigates to index."""
        page.goto(f"{live_server}/session/e2e-session-1")
        
        page.locator("a:has-text('Back to all sessions')").click()
        
        # Should be on index
        assert page.url == f"{live_server}/"

    def test_code_blocks_rendered(self, live_server, page):
        """Test that code blocks are properly rendered."""
        page.goto(f"{live_server}/session/e2e-session-1")
        
        # Check for code blocks
        code_blocks = page.locator("pre code")
        assert code_blocks.count() > 0

    def test_markdown_newlines_preserved(self, live_server, page):
        """Test that newlines in markdown are preserved as line breaks."""
        page.goto(f"{live_server}/session/e2e-session-1")
        
        # The numbered list items should be on separate lines
        content = page.locator(".message-content").first
        html = content.inner_html()
        
        # Should have line breaks or paragraph structure
        assert "<br" in html.lower() or "<p>" in html.lower() or "<li>" in html.lower()


class TestErrorHandling:
    """Playwright tests for error handling."""

    def test_404_for_missing_session(self, live_server, page):
        """Test that missing session returns 404 page."""
        response = page.goto(f"{live_server}/session/nonexistent-session")
        
        assert response.status == 404
        assert page.locator("text=Session not found").is_visible()

    def test_404_has_back_link(self, live_server, page):
        """Test that 404 page has link back to index."""
        page.goto(f"{live_server}/session/nonexistent-session")
        
        back_link = page.locator("a:has-text('Back to all sessions')")
        assert back_link.is_visible()
        
        back_link.click()
        assert page.url == f"{live_server}/"


class TestWorkspaceFilter:
    """Playwright tests for workspace filter functionality."""

    def test_workspace_checkboxes_displayed(self, live_server, page):
        """Test that workspace filter checkboxes are displayed."""
        page.goto(live_server)
        
        # Check that workspace checkboxes exist
        my_workspace_checkbox = page.locator("input[type='checkbox'][value='my-workspace']")
        another_project_checkbox = page.locator("input[type='checkbox'][value='another-project']")
        
        assert my_workspace_checkbox.is_visible()
        assert another_project_checkbox.is_visible()

    def test_apply_filter_button_exists(self, live_server, page):
        """Test that Apply Filter and Clear buttons exist."""
        page.goto(live_server)
        
        apply_btn = page.locator("button:has-text('Apply Filter')")
        clear_btn = page.locator("button:has-text('Clear')")
        
        assert apply_btn.is_visible()
        assert clear_btn.is_visible()

    def test_filter_by_workspace(self, live_server, page):
        """Test that selecting a workspace and applying filter works."""
        page.goto(live_server)
        
        # Initially both sessions should be visible
        assert page.locator("a:has-text('VS Code debug')").is_visible()
        assert page.locator("a:has-text('another-project')").is_visible()
        
        # Check my-workspace checkbox
        page.locator("input[type='checkbox'][value='my-workspace']").check()
        
        # Click Apply Filter
        page.locator("button:has-text('Apply Filter')").click()
        
        # URL should have workspace parameter
        assert "workspace=my-workspace" in page.url
        
        # Only my-workspace session should be visible
        assert page.locator("a:has-text('VS Code debug')").is_visible()
        assert not page.locator("a:has-text('another-project')").is_visible()

    def test_clear_filter(self, live_server, page):
        """Test that Clear button removes all filters."""
        # Start with a filter applied
        page.goto(f"{live_server}/?workspace=my-workspace")
        
        # Only one session should be visible
        assert page.locator("a:has-text('VS Code debug')").is_visible()
        assert not page.locator("a:has-text('another-project')").is_visible()
        
        # Click Clear button
        page.locator("button:has-text('Clear')").click()
        
        # URL should not have workspace parameter
        assert "workspace=" not in page.url
        
        # Both sessions should be visible
        assert page.locator("a:has-text('VS Code debug')").is_visible()
        assert page.locator("a:has-text('another-project')").is_visible()

    def test_filter_persists_in_url(self, live_server, page):
        """Test that filter state persists in URL."""
        page.goto(f"{live_server}/?workspace=another-project")
        
        # Checkbox should be checked
        checkbox = page.locator("input[type='checkbox'][value='another-project']")
        assert checkbox.is_checked()
        
        # Only another-project session should be visible
        assert page.locator("a:has-text('another-project')").is_visible()
        assert not page.locator("a:has-text('VS Code debug')").is_visible()
