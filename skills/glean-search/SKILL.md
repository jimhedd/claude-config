---
name: glean-search
description: Search internal company documents, code repositories, and knowledge bases using Glean MCP tools. Supports document discovery, code search, and full document retrieval.
---

# Glean Search Skill

This skill provides access to Glean's enterprise search capabilities through MCP tools. Use these tools to search internal company documents, code repositories, and knowledge bases.

## MCP Server Reference

**Server:** `glean` (configured in `.mcp.json`)

All tools must use fully qualified names with the `glean:` prefix to avoid "tool not found" errors.

---

## Authentication

**Before using Glean tools, verify the MCP server is authenticated.**

### Check Authentication

Attempt a simple search to test the connection:

```text
Use the glean:search tool with:
- query: "test"
```

### If Authentication Fails

If you receive an authentication error, permission denied, or connection failure:

1. **Prompt the user to authenticate:**

   > "The Glean MCP server requires authentication. Please authenticate before I can search internal documentation.
   >
   > To authenticate:
   > 1. Verify your `.mcp.json` configuration points to the correct Glean server
   > 2. Complete Glean SSO authentication in your browser if prompted
   > 3. Ensure your Glean account has access to the requested content
   >
   > Let me know once you've authenticated and I'll retry."

2. **Graceful degradation:** If the user cannot authenticate, note the limitation and continue with alternative data sources (e.g., PR descriptions, public documentation).

3. **Common authentication errors:**
    - `401 Unauthorized` - Credentials missing or expired
    - `403 Forbidden` - Valid credentials but no access to resource
    - `Connection refused` - MCP server not running or misconfigured

---

## Available Tools

| Tool | Full Name | Purpose |
|------|-----------|---------|
| Search | `glean:search` | Document discovery with filters |
| Code Search | `glean:code_search` | Internal code repositories |
| Read Document | `glean:read_document` | Full content retrieval by URL |

---

## Tool 1: glean:search

**Purpose:** Search internal company documents, files, wikis, and knowledge bases.

**When to use:**
- Finding documents, policies, specifications, or procedures by keywords
- Locating files, presentations, or resources with metadata filters
- Getting document snippets, titles, URLs, and metadata
- Filtering by author, date, channel, or document type

**When NOT to use:**
- For analysis or synthesis across multiple sources (use `glean:chat` instead)
- For code-specific searches (use `glean:code_search`)

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | Keywords to find documents. Use `*` for all documents with filters. No quotes. |
| `owner` | No | Documents created by person. Values: name, "me", "myteam" |
| `from` | No | Documents updated/commented/created by person. Values: name, "me", "myteam" |
| `app` | No | Filter by app: github, confluence, gdrive, slack, jira, notion, etc. |
| `type` | No | Document type: "pull", "spreadsheet", "slides", "email", "direct message", "folder" |
| `updated` | No | Date filter: "today", "yesterday", "past_week", "past_2_weeks", "past_month", "March" |
| `after` | No | Documents after date (YYYY-MM-DD format) |
| `before` | No | Documents before date (YYYY-MM-DD format) |
| `channel` | No | Filter to specific channel |
| `sort_by_recency` | No | Sort by newest first. Only use for "latest" or "most recent" queries |
| `exhaustive` | No | Get all results. Only use when user requests "all", "each", "every" |

### Example Usage

```
Use the glean:search tool with:
- query: "onboarding documentation"
- owner: "me"
- updated: "past_month"
```

### Workflow

1. Search with initial query and filters
2. Review results for relevant documents
3. Use `glean:read_document` with URLs to get full content

---

## Tool 2: glean:code_search

**Purpose:** Search internal company code repositories and private commits.

**When to use:**
- Finding proprietary code implementations, functions, or classes
- Understanding internal features and software architecture
- Locating specific code patterns or API usage examples
- Searching code content and file paths

**Scope:** Internal company repositories only - NOT for public/open-source code.

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | Search query with keywords and optional filters |

### Query Filters

| Filter | Description | Example |
|--------|-------------|---------|
| `owner:` | Commits by person | `owner:"Andy"`, `owner:"me"` |
| `from:` | Files updated/created by person | `from:"me"` |
| `updated:` | Files updated on/after date | `updated:past_week`, `updated:today` |
| `after:` | Files changed after date | `after:2024-01-15` |
| `before:` | Files changed before date | `before:2024-12-31` |

### Example Usage

```
Use the glean:code_search tool with:
- query: "function getUserData"
```

```
Use the glean:code_search tool with:
- query: "owner:\"me\" updated:past_week"
```

### Workflow

1. Search with code keywords and filters
2. Review file paths and snippets in results
3. Use `glean:read_document` with file URLs to view complete source files

---

## Tool 3: glean:read_document

**Purpose:** Retrieve full content of specific documents by URL.

**When to use:**
- Reading complete document content when you have exact URLs
- Getting full text of documents found via search results
- Fetching multiple specific documents in one request
- Accessing detailed document content for analysis

**Requires:** Valid URLs from Glean-indexed documents (typically from search results).

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `urls` | Yes | Array of document URLs to retrieve |

### Example Usage

```
Use the glean:read_document tool with:
- urls: ["https://confluence.company.com/display/ENG/API+Guide"]
```

```
Use the glean:read_document tool with:
- urls: ["https://docs.google.com/document/d/abc123", "https://notion.so/Product-Spec-def456"]
```

### Response

The tool returns a detailed status report showing which URLs succeeded and which failed, with reasons for failures.

---

## Saving Search Results to Memory

When instructed to save gathered information for future use, store results in the memories directory.

### Directory Structure

```
.claude/memories/<github_username>/
├── glean_searches/
│   ├── <topic>_search_results.json    # Raw search results
│   ├── <topic>_documents.json         # Full document content
│   └── <topic>_summary.md             # Summary of findings
```

### How to Save Results

1. **Get GitHub username:**
   ```bash
   gh api user --jq '.login'
   ```

2. **Create directory:**
   ```bash
   mkdir -p .claude/memories/<username>/glean_searches
   ```

3. **Save search results:**
    - Store raw JSON results for programmatic access
    - Save key documents content for reference
    - Create a summary markdown file for human-readable overview

### Example Workflow

```bash
# 1. Get username
USERNAME=$(gh api user --jq '.login')

# 2. Create memory directory
mkdir -p .claude/memories/$USERNAME/glean_searches

# 3. After performing searches, save results to:
# .claude/memories/$USERNAME/glean_searches/api_docs_search_results.json
# .claude/memories/$USERNAME/glean_searches/api_docs_summary.md
```

### Summary File Format

Create a markdown summary with:

```markdown
# Glean Search: [Topic]

**Search Date:** YYYY-MM-DD
**Query:** [original query]
**Filters:** [any filters used]

## Key Findings

1. [Finding 1]
2. [Finding 2]

## Relevant Documents

| Title | URL | Summary |
|-------|-----|---------|
| Doc 1 | [link] | Brief summary |
| Doc 2 | [link] | Brief summary |

## Notes

Additional context or observations.
```

---

## Best Practices

### Tool Selection

1. **Start with `glean:search`** for document discovery
2. **Use `glean:code_search`** specifically for code repositories
3. **Follow up with `glean:read_document`** to get full content

### Query Optimization

1. Use specific keywords, not vague phrases
2. Combine keywords with filters for precise results
3. If too many results, add filters (owner, date, app)
4. If too few results, broaden query or remove filters

### Enterprise Context

- Results are filtered by your access permissions
- Empty results may mean you lack access, not that documents don't exist
- Treat all returned information as internal company data

### Source Citation

When using search results in responses:
- Include document URLs so users can verify information
- Cite specific documents by title when referencing content
- Note when information comes from multiple sources

---

## Error Handling

### No Results Found

1. Try alternative keywords or synonyms
2. Remove restrictive filters
3. Check if a different tool is more appropriate
4. Ask user to verify the topic exists in internal systems

### Permission Errors

- Results are filtered by user permissions
- If expected documents are missing, user may lack access
- Suggest user contact document owner for access

### Tool Not Found

Ensure using fully qualified tool names:
- Correct: `glean:search`
- Wrong: `search` (missing server prefix)
