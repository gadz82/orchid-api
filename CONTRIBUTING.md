# Contributing to orchid-api

## Development Setup

```bash
cd orchid-api
python -m venv .venv
source .venv/bin/activate
pip install -e ../orchid -e ".[dev]"
pre-commit install
```

The last command installs git hooks that **automatically run ruff (lint + format) and gitlint (commit message check) before every commit**.

## Commit Message Convention

This project uses **[Conventional Commits](https://www.conventionalcommits.org/)** to enable automatic semantic versioning. Every commit message must follow this format:

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Types

| Type | Description | Version Bump |
|------|-------------|-------------|
| `feat` | New feature | **minor** (0.X.0) |
| `fix` | Bug fix | **patch** (0.0.X) |
| `perf` | Performance improvement | **patch** (0.0.X) |
| `refactor` | Code refactor (no feature/fix) | none |
| `docs` | Documentation only | none |
| `style` | Formatting, whitespace | none |
| `test` | Adding/updating tests | none |
| `build` | Build system, dependencies | none |
| `ci` | CI/CD configuration | none |
| `chore` | Maintenance tasks | none |

### Breaking Changes

Append `!` after the type or add `BREAKING CHANGE:` in the footer for a **major** bump:

```
feat!: change /chats/{id}/messages response format
```

### Examples

```
feat(routers): add pagination to GET /chats endpoint
fix(auth): handle expired bearer tokens gracefully
docs: update API endpoint table in README
test(sharing): add tests for scope promotion endpoint
ci: add coverage reporting to GitLab pipeline
```

### Validation

Commit messages are validated in CI via [gitlint](https://jorisroovers.com/gitlint/). To check locally:

```bash
pip install gitlint
gitlint
```

## Running Tests

```bash
pytest tests/ -x                       # all tests
pytest tests/ --cov=orchid_api         # with coverage
ruff check orchid_api/                 # lint
ruff format orchid_api/                # format
```

## Merge Requests

1. Create a feature branch from `main`
2. Use conventional commit messages
3. Ensure tests pass and linting is clean
4. Keep MRs focused -- one feature or fix per MR
