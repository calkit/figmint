# Contributing to `fromwhere`

Thank you for considering contributing to `fromwhere`!
We welcome contributions of all kinds, including code, documentation,
bug reports, and feature suggestions.
This guide will help you get started.

## 🛠 How to contribute

### 1. Find an issue to work on

- Check the **[backlog](https://github.com/orgs/calkit/projects/1/views/1)**
  for issues that are ready to be worked on.
  Filter for the word "fromwhere".
- Look for issues labeled `good first issue` if you're new.
- If you have an idea, open a new issue and discuss it before coding.

### 2. Set up your development environment

1. [**Fork** the repository](https://github.com/calkit/fromwhere/fork).
1. **Clone** your fork locally:
   ```bash
   git clone https://github.com/{your-username}/fromwhere.git
   cd calkit
   ```
1. **Install system-level dependencies:**
   - [uv](https://docs.astral.sh/uv/getting-started/installation/)
1. **Run tests** to ensure everything is working:
   ```bash
   uv run pytest
   ```
1. Install the `fromwhere` CLI in dev mode:
   ```sh
   uv tool install -e .
   ```
1. **Set up pre-commit hooks** to auto-enforce code style standards:
   ```bash
   uv tool install prek
   prek install
   ```
   This will run automated checks before each commit.

### 3. Make your changes

- Create a new branch:
  ```bash
  git checkout -b your-feature-name
  ```
- Fix automatically-checked formatting issues:
  ```bash
  make format
  ```
- Follow style guidelines not automatically checked:
  - No blank lines inside functions/methods
  - Type hints required for all functions
  - NumPy-style docstrings
- Commit your changes
  (use the imperative mood and capitalize the first letter,
  but don't use punctuation):
  ```bash
  git add .
  git commit -m "Short description of your change"
  ```
- Push your branch:
  ```bash
  git push origin your-feature-name -u
  ```

### 4. Submit a pull request (PR)

- Open a **Pull request** on GitHub.
- Link the PR to the issue it resolves by adding "resolves #{issue number}"
  to the description.
- Wait for a review and make necessary changes.

## 🚀 Releasing

Releases are cut from GitHub, not from a laptop.
There is no version to bump: `pyproject.toml` declares the version dynamic
and [hatch-vcs](https://github.com/ofek/hatch-vcs) derives it from the git
tag, so tagging _is_ the release.
[Draft a new release](https://github.com/calkit/fromwhere/releases/new) with a
tag of the form `v0.2.0` and publish it.

Between releases the version is a development one derived from the last tag
and the commit, e.g. `0.2.1.dev4+g1a2b3c4`, which is what `fromwhere --version`
reports from a working copy.

Publishing the release runs the `Publish to PyPI` workflow, which builds the
wheel and the source tarball with `uv build` and uploads them with
[trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API
token is stored anywhere.
PyPI needs a trusted publisher configured for the `fromwhere` project
pointing at the `publish.yml` workflow, and the repository needs a matching
`pypi` environment.

The distribution, the import name, the CLI, and this repository are all
`fromwhere`, with no split between them.

This project was called Figmint through `v0.0.1a`, when it was narrower than it
turned out to be—the name promised figures, and what it actually tracks is any
artifact.
The record it keeps is now `provenance.toml` rather than `figmint.toml`,
deliberately named for the concept instead of for this tool, so that other
tools can read and write it.
A `figmint.toml` left over from before is still read, with a warning, and is
replaced by `provenance.toml` the next time anything is recorded.

## 💡 Other ways to contribute

- **Report bugs**: Open an issue with detailed reproduction steps.
- **Improve documentation**: Help us make Calkit's docs better.
- **Suggest features**: Share your ideas for improvements.

## 🎉 Join the community

- Participate in **[GitHub Discussions](https://github.com/calkit/discussions)**.
- Join our [**Discord**](https://discord.gg/m2MBC79HzD) for real-time collaboration.
- Follow our updates on [**LinkedIn**](https://linkedin.com/company/calkit).

We appreciate your help in making `fromwhere` better!
