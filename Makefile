# Local entry points for the repository checks.
#
#   make check      # everything that CI runs on a pull request, no network needed
#   make links      # real HTTP check of every link (needs network)
#   make fix        # regenerate the table of contents, clean whitespace
#   make sort       # re-sort every section alphabetically
#   make test       # self-tests for the checkers
#
# `make check` is the one to run before pushing.

PYTHON ?= python3
FILES  := README.md CONTRIBUTING.md CODE_OF_CONDUCT.md

.PHONY: help check lint lint-strict links local-links fix sort test pr-title clean

help:
	@grep -E '^[a-zA-Z-]+:.*?## ' $(MAKEFILE_LIST) | sed 's/:.*## / — /'

check: lint local-links test ## Lint, check local links and run the tests

lint: ## Lint README structure, ordering and table of contents
	$(PYTHON) scripts/lint_readme.py README.md

lint-strict: ## Same as lint, but warnings fail too (what CI runs)
	$(PYTHON) scripts/lint_readme.py README.md --strict --github-annotations

local-links: ## Verify local files, images and anchors without network access
	$(PYTHON) scripts/check_links.py $(FILES) --local-only

links: ## Check every link over HTTP
	$(PYTHON) scripts/check_links.py $(FILES)

fix: ## Auto-fix the table of contents and whitespace
	$(PYTHON) scripts/lint_readme.py README.md --fix

sort: ## Re-sort all entries alphabetically, then fix the TOC
	$(PYTHON) scripts/lint_readme.py README.md --fix --sort

test: ## Run the checker self-tests
	$(PYTHON) -m unittest discover -s tests -v

pr-title: ## Validate a PR title, e.g. `make pr-title TITLE="Add Clay"`
	$(PYTHON) scripts/check_pr_title.py --title "$(TITLE)"

clean: ## Remove generated reports
	rm -f link-report.md
