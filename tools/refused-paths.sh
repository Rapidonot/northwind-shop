#!/bin/bash
# AUTO-1 (decision 0008): refuse changes that would run outside the reviewed diff once they land in the
# runner clone. Called by `ship` and `runner-sync` as refused-paths.sh <base> <head>, from the clone, so
# the checked-out .gitignore and this copy are the reviewed ones, never the incoming ones.
#
# GNU make reads GNUmakefile before Makefile; the Makefile includes the local env file as make syntax;
# `git merge` overwrites ignored files, so a tracked .pth reaches .venv-runner. None of these shows in
# the full diff of RUNNER_SYNC_PATHS. Keep the patterns in step with tools/pre-push.proposed.sh; the
# tests run one table against both.
#
# Printable ASCII only: case-insensitive APFS folds more than ASCII, so a path spelled with U+017F (long
# s) lands in `services/` while every ASCII match here and in git's pathspecs sees another directory.
# Measured 2026-11-05: `git merge --ff-only` wrote such a file into `services/`. No tracked path needs it.
set -euo pipefail
export LC_ALL=C

refuse() {
	printf 'refused: %s\n' "$*" | cat -v >&2
	exit 1
}

[[ $# -eq 2 ]] || refuse "usage: refused-paths.sh <base> <head>"
base=$1
head=$2

work=$(mktemp -d "${TMPDIR:-/tmp}/refused.XXXXXX")
trap 'rm -rf "$work"' EXIT

git --no-pager diff --text --no-ext-diff --no-textconv --no-renames --name-only -z "$base" "$head" \
	>"$work/paths" || refuse "git diff failed"

: >"$work/parsed"
: >"$work/hits"
while IFS= read -r -d '' path; do
	printf '%s\0' "$path" >>"$work/parsed"
	if [[ "$path" == *[![:print:]]* ]]; then
		printf '%s\n' "${path//$'\n'/\\n}" >>"$work/hits"
		continue
	fi
	name=${path##*/}
	case "$name" in
	Makefile) ;;
	[Gg][Nn][Uu][Mm][Aa][Kk][Ee][Ff][Ii][Ll][Ee] | [Mm][Aa][Kk][Ee][Ff][Ii][Ll][Ee])
		printf '%s\n' "${path//$'\n'/\\n}" >>"$work/hits"
		continue
		;;
	esac
	case "/$path" in
	*/.[Ee][Nn][Vv]* | */.[Vv][Ee][Nn][Vv]*)
		printf '%s\n' "${path//$'\n'/\\n}" >>"$work/hits"
		;;
	esac
done <"$work/paths"

if [[ -s "$work/hits" ]]; then
	cat -v "$work/hits" >&2
	refuse "these paths must not reach the runner clone"
fi

# Exit 0 means something is ignored, 1 means nothing is, anything else is an error.
if [[ -s "$work/parsed" ]]; then
	rc=0
	git check-ignore --no-index -z --stdin <"$work/parsed" >"$work/ignored" || rc=$?
	case "$rc" in
	0)
		tr '\0' '\n' <"$work/ignored" | cat -v >&2
		refuse "these paths are ignored by the checked-out .gitignore"
		;;
	1) ;;
	*) refuse "git check-ignore failed" ;;
	esac
fi

# On a case-insensitive filesystem two such paths are one file, and the second overwrites the first.
git ls-tree -r --name-only -z "$head" >"$work/tree" || refuse "git ls-tree failed"
tr '\0' '\n' <"$work/tree" | LC_ALL=C tr '[:upper:]' '[:lower:]' | LC_ALL=C sort | uniq -d >"$work/folded"
if [[ -s "$work/folded" ]]; then
	cat -v "$work/folded" >&2
	refuse "these paths differ only by case"
fi
