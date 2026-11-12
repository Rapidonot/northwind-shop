#!/bin/bash
# AUTO-1: check a PR, wait for CI, ask, merge. `make ship PR=<n> SHA=<reviewed>` calls this from the
# runner clone with RUNNER_SYNC_PATHS as its last arguments, then runs runner-sync. This holds the user's
# GitHub login, so it is in RUNNER_SYNC_PATHS itself. Written for macOS bash 3.2: no ${x,,}, no mapfile.
#
# Every failure refuses. Paths, check names and labels are text a PR controls: they are shown through
# `cat -v` with newlines escaped, and never reach a shell.
#
# Minimum gh: 2.103.3, the oldest version these flags were read from. It may be stricter than needed;
# the release that added `gh pr checks --json` is not established. check_gh also probes the flags.
set -euo pipefail

GH_MIN=2.103.3
# workflow/job. Guard's jobs run main's evals/src, CI's run the PR's own (decision 0009).
REQUIRED_JOBS="CI/python CI/eval-gate CI/e2e CI/portal Guard/baseline-label Guard/eval-integrity-main Guard/eval-gate-main"
MAIN_REF=refs/runner/ship-main
HEAD_REF=refs/runner/ship-head
# --icase-pathspecs: `Services/common/x.py` must count as a sync path on a case-insensitive clone.
GIT_DIFF=(git --no-pager --icase-pathspecs diff --text --no-ext-diff --no-textconv --no-renames)
NAMES=("${GIT_DIFF[@]}" --name-only -z)

refuse() {
	printf 'ship: refused: %s\n' "$*" | cat -v >&2
	exit 1
}

# `--labels <base> <head>`: which labels this diff needs, for `finish`, so that one classifier decides.
# Local git only: it returns below, before the first gh call.
labels_only=0
if [[ "${1:-}" == --labels ]]; then
	[[ $# -eq 3 && "$2" =~ ^[0-9a-f]{40}$ && ("$3" =~ ^[0-9a-f]{40}$ || "$3" == "$HEAD_REF") ]] ||
		refuse "usage: ship.sh --labels <base-sha> <head-sha or $HEAD_REF>"
	labels_only=1
	bases=$2
	labels_head=$3
else
	[[ $# -ge 3 && "$1" =~ ^[0-9]+$ && "$2" =~ ^[0-9a-f]{40}$ ]] ||
		refuse "usage: ship.sh <pr-number> <reviewed-sha> <sync-pathspec>..."
	pr=$1
	reviewed=$2
	shift 2
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/ship.XXXXXX")
trap 'rm -rf "$work"' EXIT

check_gh() {
	local line re='^gh version ([0-9]+)\.([0-9]+)\.([0-9]+)' help i
	local -a have want
	command -v gh >/dev/null || refuse "gh is not installed"
	line=$(gh --version) || refuse "gh --version failed"
	[[ "$line" =~ $re ]] || refuse "cannot read the gh version"
	have=("${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}")
	IFS=. read -r -a want <<<"$GH_MIN"
	for i in 0 1 2; do
		if ((10#${have[i]} > 10#${want[i]})); then break; fi
		if ((10#${have[i]} < 10#${want[i]})); then refuse "gh $GH_MIN or newer is needed"; fi
	done
	help=$(gh pr checks --help 2>&1) || refuse "gh pr checks --help failed"
	[[ "$help" == *--watch* && "$help" == *--json* && "$help" == *bucket* ]] ||
		refuse "this gh has no structured gh pr checks output"
	help=$(gh pr merge --help 2>&1) || refuse "gh pr merge --help failed"
	[[ "$help" == *--match-head-commit* ]] || refuse "this gh has no --match-head-commit"
}

# One gh call, so the fields describe one moment. Labels are the lines after the first five.
snapshot() {
	gh pr view "$pr" --json state,baseRefName,isDraft,isCrossRepository,headRefOid,labels \
		--jq '.state, .baseRefName, .isDraft, .isCrossRepository, .headRefOid, (.labels[].name)' \
		>"$work/snapshot" || refuse "gh pr view failed"
	{
		read -r state
		read -r base
		read -r draft
		read -r fork
		read -r head
	} <"$work/snapshot" || refuse "gh pr view gave too little"
	[[ "$state" == OPEN ]] || refuse "PR #$pr is not open"
	[[ "$base" == main ]] || refuse "PR #$pr does not target main"
	[[ "$draft" == false ]] || refuse "PR #$pr is a draft"
	[[ "$fork" == false ]] || refuse "PR #$pr comes from a fork"
	[[ "$head" =~ ^[0-9a-f]{40}$ ]] || refuse "PR #$pr has no usable head commit"
}

# Exact and case-sensitive. nocasematch is on only inside classify, so it cannot reach this.
has_label() {
	local line n=0
	while IFS= read -r line; do
		n=$((n + 1))
		if ((n > 5)) && [[ "$line" == "$1" ]]; then return 0; fi
	done <"$work/snapshot"
	return 1
}

# From the merge base with a fresh main, not gh's file list, which hides the old side of a rename.
# --refmap= keeps origin/main where it is: runner preflight() refuses a clone that has moved off it.
changed_paths() {
	git fetch --quiet --no-tags --refmap= origin "+refs/heads/main:$MAIN_REF" \
		"+refs/pull/$pr/head:$HEAD_REF" || refuse "git fetch failed"
	[[ "$(git rev-parse "$HEAD_REF")" == "$head" ]] || refuse "fetched head is not the PR's head"
	bases=$(git merge-base --all "$MAIN_REF" "$HEAD_REF") || refuse "no merge base with main"
	[[ "$bases" =~ ^[0-9a-f]{40}$ ]] || refuse "no single merge base with main"
	"${NAMES[@]}" "$bases" "$HEAD_REF" >"$work/paths" || refuse "git diff failed"
	[[ -s "$work/paths" ]] || refuse "PR #$pr changes no files"
	"${NAMES[@]}" "$bases" "$HEAD_REF" -- "$@" >"$work/sync" || refuse "git diff failed"
}

# What the reviewers read must be what merges, apart from the docs `/req-done` writes afterwards.
check_reviewed() {
	local path
	git merge-base --is-ancestor "$reviewed" "$HEAD_REF" || refuse "the reviewed commit is not in PR #$pr"
	"${NAMES[@]}" "$reviewed" "$HEAD_REF" >"$work/later" || refuse "git diff failed"
	while IFS= read -r -d '' path; do
		case "$path" in
		PROGRESS.md | COVERAGE.md) ;;
		*) refuse "changed since the reviewed commit: ${path//$'\n'/\\n}" ;;
		esac
	done <"$work/later"
}

# -z, because git quotes unusual paths otherwise and a quoted path misses every pattern below.
classify() {
	local path
	baseline_hit=0
	ci_hit=0
	gate_hit=0
	: >"$work/surface"
	: >"$work/labels"
	shopt -s nocasematch
	while IFS= read -r -d '' path; do
		case "$path" in
		evals/baselines | evals/baselines/* | evals/fixtures/known_failures.json | \
			evals/fixtures/starter_set.jsonl)
			baseline_hit=1
			printf 'baseline-change\t%s\n' "${path//$'\n'/\\n}" >>"$work/labels"
			;;
		esac
		case "$path" in
		.github/workflows | .github/workflows/*)
			ci_hit=1
			printf 'ci-change\t%s\n' "${path//$'\n'/\\n}" >>"$work/labels"
			;;
		esac
		case "$path" in
		evals/src | evals/src/*) gate_hit=1 ;;
		esac
		case "$path" in
		.github/workflows | .github/workflows/* | .claude | .claude/* | CLAUDE.md | Makefile | \
			tools/*.proposed* | tools/ship.sh | tools/refused-paths.sh | \
			evals/baselines | evals/baselines/* | evals/src | evals/src/* | evals/fixtures | evals/fixtures/* | \
			cassettes | cassettes/* | data/enrichment | data/enrichment/* | data/embeddings | data/embeddings/* | evals/adversarial | evals/adversarial/* | conftest.py | */conftest.py | .gitignore | */.gitignore | \
			pyproject.toml | package.json | */package.json)
			printf '%s\0' "$path" >>"$work/surface"
			;;
		esac
	done <"$work/paths"
	shopt -u nocasematch
	cat "$work/sync" >>"$work/surface"
}

# The last time each label was added must be by this account, not a bot: a workflow on a pushed branch
# can hold a token that applies labels. A later change to the PR is caught by check_reviewed.
check_label_actor() {
	local me stamp name who kind last_who="" last_kind=""
	me=$(gh api user --jq .login) || refuse "gh api user failed"
	[[ "$me" =~ ^[A-Za-z0-9-]+$ ]] || refuse "cannot read your GitHub login"
	gh api --paginate "repos/{owner}/{repo}/issues/$pr/events" \
		--jq '.[] | select(.event == "labeled") | [.created_at, .label.name, .actor.login, .actor.type] | @tsv' \
		>"$work/events" || refuse "gh api issue events failed"
	LC_ALL=C sort -s -t $'\t' -k 1,1 "$work/events" >"$work/events.sorted"
	while IFS=$'\t' read -r stamp name who kind; do
		if [[ "$name" == "$1" ]]; then
			last_who=$who
			last_kind=$kind
		fi
	done <"$work/events.sorted"
	[[ "$last_who" == "$me" && "$last_kind" == User ]] ||
		refuse "the $1 label was not last added by your account"
}

# ci.yml runs on pull_request, so a PR is tested by its own workflow: green CI proves nothing there.
check_labels() {
	if ((baseline_hit)); then
		has_label baseline-change ||
			refuse "changes evals/baselines, known_failures.json or the item set without the baseline-change label"
		check_label_actor baseline-change
	fi
	if ((ci_hit)); then
		has_label ci-change || refuse "changes .github/workflows without the ci-change label"
		check_label_actor ci-change
	fi
	if ((gate_accepted)); then
		has_label gate-change || refuse "the gate-change label went during the wait"
		check_label_actor gate-change
	fi
}

# The exit code of `gh pr checks` says nothing here: --json returns 0 with failing checks, and --watch
# cannot be combined with it. So --watch only waits, and the buckets decide. A push and a pull_request
# run can report the same job, so every entry must pass, and each required job must exist in its
# workflow. Any other check that did not pass refuses too, so a new job counts from its first run.
#
# The one exception is main's gate failing on a PR that changes the gate, as row #8 must. A real change
# to evals/src can make main's copy fail on purpose, so a `gate-change` label added by your account
# accepts that one failure. Only `fail`: `pending` and `cancel` prove nothing. Never integrity. CI holds
# no waiver, because that file is PR-editable and a re-run keeps its old labels (decision 0009).
accepts_failure() {
	[[ "$1" == Guard && "$2" == eval-gate-main && "$3" == fail ]] || return 1
	((gate_hit)) || return 1
	has_label gate-change
}

check_jobs() {
	local job wf name bucket seen
	gh pr checks "$pr" --watch --interval 30 2>&1 | cat -v || true
	gh pr checks "$pr" --json name,bucket,workflow --jq '.[] | [.workflow, .name, .bucket] | @tsv' \
		>"$work/checks" || refuse "gh pr checks failed"
	while IFS=$'\t' read -r wf name bucket; do
		if [[ "$bucket" == pass ]]; then continue; fi
		accepts_failure "$wf" "$name" "$bucket" || refuse "check $name is $bucket"
		gate_accepted=1
	done <"$work/checks"
	for job in $REQUIRED_JOBS; do
		seen=0
		while IFS=$'\t' read -r wf name bucket; do
			if [[ "$wf/$name" == "$job" ]]; then seen=1; fi
		done <"$work/checks"
		((seen)) || refuse "job $job is missing"
	done
}

show_list() {
	local path
	if [[ -s "$1" ]]; then
		while IFS= read -r -d '' path; do
			printf '%s\n' "${path//$'\n'/\\n}"
		done <"$1" | LC_ALL=C sort -u | cat -v
	else
		echo "(none)"
	fi
}

if ((labels_only)); then
	"${NAMES[@]}" "$bases" "$labels_head" >"$work/paths" || refuse "git diff failed"
	: >"$work/sync"
	classify
	cat "$work/labels"
	exit 0
fi

gate_accepted=0
check_gh
snapshot
first_head=$head
changed_paths "$@"
check_reviewed
/bin/bash tools/refused-paths.sh "$bases" "$HEAD_REF" || refuse "see above"
classify

echo "ship: files changed by PR #$pr:"
show_list "$work/paths"
check_labels

check_jobs

# Anything can change during the wait. The merge below also pins the head.
snapshot
[[ "$head" == "$first_head" ]] || refuse "the PR's head moved during the wait"
check_labels

# Label-gated paths too: a label says the user accepted a change, not this change.
echo "ship: full diff of the paths that load with the key, run with your login, or need a label:"
"${GIT_DIFF[@]}" "$bases" "$HEAD_REF" -- "$@" evals/baselines evals/fixtures/known_failures.json \
	.github/workflows evals/src | cat -v || refuse "git diff failed"
# The only files allowed to move after the review, and PROGRESS.md is imported into CLAUDE.md.
echo "ship: PROGRESS.md and COVERAGE.md since the reviewed commit:"
"${GIT_DIFF[@]}" "$reviewed" "$HEAD_REF" -- PROGRESS.md COVERAGE.md | cat -v || refuse "git diff failed"
if ((gate_accepted)); then
	echo "ship: main's gate did not pass on this PR, accepted by gate-change. Green here means only that the"
	echo "ship: PR's own gate passed, not that the gate is intact: read the evals/src diff above."
fi
echo "ship: control surface changed by PR #$pr, read these before answering:"
show_list "$work/surface"

# Drop anything typed during the wait: a stray y must not answer a question not yet asked.
while IFS= read -r -t 1 -n 1 junk; do :; done

if [[ -s "$work/surface" ]]; then
	printf 'Type the PR number to merge PR #%s at %s: ' "$pr" "$head"
	IFS= read -r answer || answer=
	[[ "$answer" == "$pr" ]] || refuse "not merged"
else
	printf 'Merge PR #%s at %s? [y/N] ' "$pr" "$head"
	IFS= read -r answer || answer=
	[[ "$answer" =~ ^[yY]$ ]] || refuse "not merged"
fi

# No --admin: that would bypass requirements. --match-head-commit refuses a push after the check.
gh pr merge "$pr" --merge --match-head-commit "$head" 2>&1 | cat -v || refuse "gh pr merge failed"
final=$(gh pr view "$pr" --json state --jq .state) || refuse "cannot confirm the merge"
[[ "$final" == MERGED ]] || refuse "PR #$pr is $final, not MERGED"
echo "ship: PR #$pr merged"
