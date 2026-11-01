# Contributing to Awesome Lead Generation

First off, thank you for considering contributing to Awesome Lead Generation. We appreciate your time and effort in helping us build the ultimate modern growth stack repository.

The goal of this project is to curate a high-quality list of AI-powered tools for lead generation, social listening, data enrichment, and automated outreach. To maintain the quality of this list, please review the guidelines below before submitting a pull request.

By participating in this project you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Submission Guidelines

Please ensure your submission adheres to the following core criteria:

- **Relevance:** The tool must have a clear AI component or be an essential part of the modern lead generation stack.
- **Active Maintenance:** We only accept active projects. Abandoned or unmaintained tools will not be merged.
- **Direct Links Only:** Absolutely no affiliate, tracking, or referral links are allowed.
- **Alphabetical Ordering:** Submissions must be placed in strict alphabetical order within their respective categories.

## Description Standards

Descriptions must be concise, objective, and purely informational.

- **Do:** Focus on the exact utility and core features of the tool.
  > *Example: "Automates LinkedIn outreach and extracts profile data."*
- **Don't:** Use marketing fluff, superlatives, or subjective claims.
  > *Example: "The world's best platform that revolutionizes your sales process."*

## Step-by-Step Submission Process

To add a new tool to the list, please follow these steps:

1. **Star the repository:** Show your support and help the community grow.
2. **Locate the file:** Navigate to the `README.md` file in the main branch of this repository.
3. **Edit the document:** Click the "Edit" (pencil) icon to begin making your changes.
4. **Format your entry:** Add your link to the appropriate category using the exact Markdown syntax below. Use the `github` icon when the primary link is a GitHub repository and the `web` icon otherwise. Always link directly to the tool's site or repository — never through search engines or redirect services:

   ```markdown
   ![github](image/alg_R.png) [Tool Name](https://github.com/org/repo) - Short, objective description of what it does.

   ![web](image/alg_I.png) [Tool Name](https://example.com) - Short, objective description of what it does.
   ```

   Keep entries in strict alphabetical order (case-insensitive) within their subsection and do not include pricing or promotional claims in descriptions.

5. **Run the checks locally:** See [Automated checks](#automated-checks) below.
6. **Create a Pull Request:** Submit your PR with a clear and descriptive title using the following format:
   `Add [Tool Name]`

## Automated checks

Every pull request runs a set of checkers. They exist so a PR cannot break the
list silently — a wrong icon, an out-of-order entry, a stale table of contents or
a dead link fails the build with a message pointing at the exact line.

Everything is plain Python 3 (standard library only), so you can run the same
checks locally before pushing:

```bash
make check          # README lint + local links + checker self-tests (no network)
make links          # real HTTP check of every link (needs network)
make fix            # regenerate the table of contents, clean whitespace
make sort           # re-sort every section alphabetically
make pr-title TITLE="Add Clay"   # validate a PR title
```

Without `make`, call the scripts directly:

```bash
python3 scripts/lint_readme.py README.md --strict
python3 scripts/check_links.py --local-only
python3 scripts/check_links.py                       # full link check
python3 scripts/lint_readme.py --list-rules          # every rule and its severity
```

### What is checked

| Checker | What it guarantees |
| --- | --- |
| `scripts/lint_readme.py` | Entry format, icon/link agreement, alphabetical order, no duplicate tools or URLs, no tracking parameters, description style, table of contents in sync with the headings, no broken anchors, repository files present |
| `scripts/check_links.py` | Every URL answers over HTTP: 404/410 fail the build, bot-protected hosts are reported but do not fail, redirects are listed; local files and heading anchors must exist |
| `scripts/check_pr_title.py` | The PR title starts with a verb (`Add`, `Update`, `Fix`, `Remove`, `Docs`, `Chore`) |

* **Lint failures** (`✖ error`) block the merge. `--strict` also blocks on
  warnings, which is what CI runs.
* **Warnings** (`⚠ warning`) are annotated in the "Files changed" tab and should
  be fixed while you are in the file.
* **Link checks on pull requests** only test the lines the PR touches, so
  pre-existing rot never blocks an unrelated contribution. A scheduled job
  re-checks the whole list every Monday.

### Hosts that block automated checks

Some sites answer machines with `403`, `429` or a login wall. Those hosts are
listed in [`.github/link-check-allowlist.txt`](.github/link-check-allowlist.txt)
with a reason and are reported as `allowlisted` instead of failing. If you
receive a "blocked" report for a legitimate site, do **not** add it to the
allowlist to silence a real 404 — only for genuine bot protection.

### If a check fails

1. Read the annotation: it names the file, the line and the rule code.
2. Run `make fix` — the table of contents and whitespace are regenerated for you.
3. Run `make sort` if the alphabetical order is the problem.
4. For a dead link, replace it with the tool's current URL (or remove the entry
   if the product is gone) and mention it in the PR description.

## Pull Request Review

Once submitted, your pull request will be reviewed. If it meets all the criteria outlined above, it will be merged. If any formatting adjustments or description changes are needed, we will leave feedback in the PR comments.

Please keep one tool per pull request. Large re-orderings, category renames and
bulk additions are easier to review as separate PRs.

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Be kind
in issues and reviews, critique entries rather than people, and report
unacceptable behavior to the maintainers as described in the Code of Conduct.

Thank you for your contributions!
