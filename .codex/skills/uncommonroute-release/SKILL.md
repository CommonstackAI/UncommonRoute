---
name: uncommonroute-release
description: Use when publishing UncommonRoute. A release is only complete after all required steps are done: version sync, validation, GitHub push/tag/release, PyPI publish, and npm publish.
---

# UncommonRoute Release

Use this skill when the user asks to release, publish, cut a version, or ship UncommonRoute.

## Release contract

A release is not done until all of these succeed:

1. Version files are updated together
2. Validation passes
3. Release commit and tag are pushed to GitHub
4. GitHub Release exists for that tag
5. PyPI is published
6. npm is published

If any one of those is missing, the release is incomplete.

## Keep the release clean

- Check `git status --short` first.
- Never sweep unrelated local dirt into the release.
- Stage only the files that belong to the release.
- If package registries already contain the intended version, do not try to overwrite it. Pick the next semver version instead.

## Version workflow

Use the bundled script to update every tracked version string together:

```bash
python3 .codex/skills/uncommonroute-release/scripts/sync_version.py <version>
```

This script updates:

- `pyproject.toml`
- `uncommon_route/cli.py`
- `uncommon_route/proxy.py`
- `uncommon_route/support.py`
- `openclaw-plugin/package.json`
- `openclaw-plugin/src/index.js`

## Validation workflow

Run the repo test suite and package checks before publishing:

```bash
PYTHONPATH=. python3 -m pytest tests -q
python3 -m build
python3 -m twine check dist/*
cd openclaw-plugin && npm pack --dry-run
```

Use a fresh build output. Do not upload stale artifacts from a previous version.

## Publish workflow

After validation passes:

1. Commit only the intended release files
2. Create tag `v<version>`
3. Push `main` and the tag
4. Create the GitHub Release and attach the Python artifacts
5. Publish PyPI
6. Publish npm

Typical commands:

```bash
git tag v<version>
git push origin main --follow-tags
gh release create v<version> dist/*.whl dist/*.tar.gz --title "v<version>" --notes "<notes>"
TWINE_USERNAME=__token__ TWINE_PASSWORD="$TWINE_PASSWORD" python3 -m twine upload dist/*
cd openclaw-plugin && npm publish --access public
```

## Verification

Check all three public destinations after publish:

- GitHub tag + Release page
- PyPI latest version
- npm latest version

Report the exact released version and any remaining caveats.
