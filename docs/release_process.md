# Release Process
Releases are automatically created using a GitHub Actions workflow that responds to pushes of annotated git tags.

## Versioning
Version numbers must be PEP440 strings: https://peps.python.org/pep-0440/

That is,
```
[N!]N(.N)*[{a|b|rc}N][.postN][.devN]
```

## Preparing for Release
1. Create a release candidate branch. This branch can be named according to the version to be released or it can simply
   be the last feature branch before releasing a new version. Regardless, this branch is used to polish
   the release, update the package metadata, etc.
   The naming convention for release-specific branches is `release/X.Y.Z`.

2. Bump the version of the package to the version you are about to release, either manually by editing `pyproject.toml`
   or by running `poetry version X.Y.Z` or bumping according to a valid bump rule like `poetry version minor`
   (see poetry docs: https://python-poetry.org/docs/cli/#version).

3. Update the version identifier in `CITATION.cff`.

4. Update `changelog.md` to reflect that the version is now "released" and revisit `README.md` to keep it up to date.

5. Open a PR to merge the release branch into main. This informs the rest of the team how the release 
   process is progressing as you polish the release branch. You may need to rebase the release branch onto 
   any recent changes to `main` and resolve any conflicts on a regular basis.

6. When you are satisfied that the release branch is ready, merge the PR into `main`. This commit should always 
   be a single commit that is a pure fast forward merge (in GitHub it will have to be a rebase and merge because
   GitHub does not support the fast-forward merge strategy).

7. Check out the `main` branch, pull the merged changes, and tag the newly created merge commit with the 
   desired version `X.Y.Z` and push the tag upstream. 

### Automatic Release Process
We use GitHub Actions for automatic release process that responds to pushes of git tags. When a tag matching 
a semantic version (`[0-9]+.[0-9]+.[0-9]+*` or `test-release/[0-9]+.[0-9]+.[0-9]+*`) is pushed, 
a workflow runs that builds the package, pushes the artifacts to PyPI or TestPyPI 
(if tag is prefixed with `test-release`), 
and creates a GitHub Release from the distributed artifacts. Release notes 
are automatically generated from commit history and the Release name is taken from the basename of the tag.

#### Official Releases
Official releases are published to the public PyPI (even if they are release candidates like `1.2.3rc1`). This differs
from test releases, which are only published to TestPyPI and are not published to GitHub at all. 
If the semantic version has any suffixes (e.g. `rc1`), the release will be marked as 
a prerelease in GitHub and PyPI.

To trigger an official release, push a tag referencing the commit you want to release. The commit _MUST_ be on 
the `main` branch. Never publish an official release from a commit that is not present in `main`!

```bash
git checkout main
git pull
git tag -a X.Y.Z -m "Version X.Y.Z"
git push origin X.Y.Z
```

#### Prereleases
Unless the pushed tag matches the regex `^[0-9]*\.[0-9]*\.[0-9]*`, the release will be marked as a
prerelease in GitHub. This allows "official" prereleases of suffixed tags, e.g. `1.2.3rc4`

#### Release Notes Generation
Release notes are generated based on commit messages since the latest non-prerelease Release.
