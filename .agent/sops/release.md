# Release Process

## Steps

1. **Bump version** in `pyproject.toml` (`version = "X.Y.Z"`)
2. **Commit and push** the version bump to `main`
3. **Create a git tag** matching the version: `git tag vX.Y.Z && git push origin vX.Y.Z`
4. **Create a GitHub release** from the tag with changelog notes
5. **Get the SHA256** of the release tarball from GitHub
6. **Update the Homebrew formula** in `Kintull/homebrew-tap`:
   - Change `url` version and `sha256` hash in `Formula/github-tracker.rb`
   - Dependencies only need updating if `pyproject.toml` dependencies changed

## Version Numbering

| Change type | Bump | Example |
|---|---|---|
| State file version bump, breaking config changes, API contract changes | **Minor** (0.X.0) | State v3->v4, renamed config keys |
| New features, new API methods, non-breaking additions | **Patch** (0.0.X) | Added deployment tracking |
| Bug fixes, test-only changes, docs | **Patch** (0.0.X) | Fixed crash on toggle |
