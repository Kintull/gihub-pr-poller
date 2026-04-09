# PR Checklist

## Before Merge

- [ ] `pytest --cov` passes with **100% coverage** (enforced by `fail_under = 100`)
- [ ] No new `pragma: no cover` without clear justification
- [ ] State file version bumped in `state.py` (`CURRENT_VERSION`) if schema changed
- [ ] New key bindings documented in `README.md` Key Bindings table
- [ ] No blocking calls in async methods (use `await`, not synchronous I/O)
- [ ] `dataclasses.replace()` used for PullRequest updates (not mutation)
- [ ] New config keys added to `DEFAULT_CONFIG`, `Config` dataclass, and `_parse_config()` with validation
- [ ] Error handling follows existing pattern: `try/except` with `logger.error()`, graceful degradation
