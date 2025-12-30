Feature: Flask Webapp for Copilot Chat Archive
  As a user of the Copilot Chat Archive
  I want to browse and search my chat sessions via a web interface
  So that I can easily find and review past conversations

  Background:
    Given a SQLite database with imported chat sessions
    And the Flask webapp is running

  # Index Page / Session Listing

  Scenario: View all sessions on the index page
    When I navigate to the index page "/"
    Then I should see a list of all chat sessions
    And each session should display its custom title as a clickable link
    And each session should show the workspace name as a property
    And each session should show the message count
    And each session should show the creation date
    And each session should show the VS Code edition badge

  Scenario: Empty database shows informative message
    Given the database has no sessions
    When I navigate to the index page "/"
    Then I should see "No sessions found. Import some chat history first!"

  # Search Functionality

  Scenario: Search for sessions using query parameter
    When I navigate to "/?q=Flask"
    Then I should see sessions containing messages that match "Flask"
    And I should see a search info box showing the query term
    And I should see up to 5 search snippets per session
    And each snippet should be a direct link to the matching message
    And snippets should ignore newlines when displaying text

  Scenario: Clear search results
    Given I am viewing search results for "Python"
    When I click "clear search"
    Then I should see all sessions
    And the search box should be empty

  Scenario: Empty search query shows all sessions
    When I navigate to "/?q="
    Then I should see all sessions

  # Session View

  Scenario: View a single session
    When I navigate to "/session/<session_id>"
    Then I should see the session title
    And I should see all messages in the session
    And I should see a "Back to all sessions" link

  Scenario: Session not found returns 404
    When I navigate to "/session/nonexistent-session-id"
    Then I should receive a 404 status code
    And I should see "Session not found"

  Scenario: Message content renders markdown correctly
    Given a session with markdown content in messages
    When I view the session
    Then code blocks should be syntax highlighted
    And inline code should be styled
    And text should preserve proper paragraph breaks
    And newlines should render as line breaks where appropriate

  Scenario: Navigate to specific message via anchor
    When I navigate to "/session/<session_id>#msg-3"
    Then the page should scroll to message 3
    And message 3 should be highlighted briefly

  # CLI Serve Command

  Scenario: Start the webapp server
    When I run "copilot-chat-archive serve"
    Then the server should start on 127.0.0.1:5000
    And I should see the startup message with database stats

  Scenario: Start server with custom options
    When I run "copilot-chat-archive serve --port 8080 --host 0.0.0.0 --db custom.db"
    Then the server should start on 0.0.0.0:8080
    And it should use the "custom.db" database

  Scenario: Serve command with missing database
    When I run "copilot-chat-archive serve --db nonexistent.db"
    Then I should see an error message
    And the exit code should be non-zero

  # Extended Message Features

  Scenario: View session with tool invocations
    Given a session with tool invocations in messages
    When I view the session
    Then I should see a collapsible "Tool Invocations" section
    And expanding it should show tool names, inputs, and results

  Scenario: View session with file changes
    Given a session with file changes in messages
    When I view the session
    Then I should see a collapsible "File Changes" section
    And expanding it should show file paths, diffs, and explanations

  Scenario: View session with thinking blocks
    Given a session with AI thinking blocks
    When I view the session
    Then thinking blocks should be collapsed by default
    And they should be visually distinct from regular content
