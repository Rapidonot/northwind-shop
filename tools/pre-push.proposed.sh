#!/bin/bash
# AUTO-1 (decision 0008): install as .git/hooks/pre-push. The push is the boundary: CI runs on `push`, so
# a session-written workflow runs with a token the moment it lands. This stops a push whose diff touches
# what should only move on purpose. Override for one push with NW_ALLOW_CONTROL=1.
#
# Self-contained on purpose: a hook that called a script in the worktree would run sandbox-written code
# in your terminal. .gitignore comes from main for the same reason, and is read in an empty repo, so a
# worktree .gitignore cannot negate it. Keep the patterns in step with tools/refused-paths.sh; the tests
# run one table against both.
#
# The sandbox can write refs and objects in the shared .git, so main's commit comes from the server, never
# from refs/remotes/origin/main, and replace refs and grafts are switched off. This is still a local check:
# it cannot be a hard boundary against something that writes .git (decision 0008).
set -euo pipefail
export LC_ALL=C GIT_NO_REPLACE_OBJECTS=1 GIT_GRAFT_FILE=/dev/null

refuse() {
	printf 'pre-push: refused: %s\n' "$*" | cat -v >&2
	exit 1
}

if [[ "${NW_ALLOW_CONTROL:-}" == 1 ]]; then exit 0; fi

work=$(mktemp -d "${TMPDIR:-/tmp}/pre-push.XXXXXX")
trap 'rm -rf "$work"' EXIT

[[ ! -e "$(git rev-parse --git-common-dir)/shallow" ]] || refuse "this clone is shallow"
listing=$(git ls-remote origin refs/heads/main) || refuse "cannot ask origin for main"
main_sha=${listing%%[[:space:]]*}
[[ "$main_sha" =~ ^[0-9a-f]{40}$ ]] || refuse "origin has no main"
git cat-file -e "$main_sha^{commit}" 2>/dev/null || refuse "run git fetch origin, then push again"

git show "$main_sha:.gitignore" >"$work/main.gitignore" || refuse "cannot read main's .gitignore"
mkdir "$work/ignore"
(
	unset GIT_DIR GIT_COMMON_DIR GIT_WORK_TREE GIT_INDEX_FILE
	git init -q --template= "$work/ignore"
) || refuse "cannot set up the ignore check"
cp "$work/main.gitignore" "$work/ignore/.gitignore"

check_range() {
	local path name rc=0
	git --no-pager diff --text --no-ext-diff --no-textconv --no-renames --name-only -z "$1" "$2" \
		>"$work/paths" || refuse "git diff failed: run git fetch origin, then push again"
	: >"$work/hits"
	while IFS= read -r -d '' path; do
		if [[ "$path" == *[![:print:]]* ]]; then
			printf '%s\n' "${path//$'\n'/\\n}" >>"$work/hits"
			continue
		fi
		name=${path##*/}
		case "$path" in
		.[Gg][Ii][Tt][Hh][Uu][Bb] | .[Gg][Ii][Tt][Hh][Uu][Bb]/*)
			printf '%s\n' "${path//$'\n'/\\n}" >>"$work/hits"
			continue
			;;
		esac
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

	if [[ -s "$work/paths" ]]; then
		(
			unset GIT_DIR GIT_COMMON_DIR GIT_WORK_TREE GIT_INDEX_FILE
			cd "$work/ignore" || exit 128
			git check-ignore --no-index -z --stdin <"$work/paths" >"$work/ignored"
		) || rc=$?
		case "$rc" in
		0) tr '\0' '\n' <"$work/ignored" >>"$work/hits" ;;
		1) ;;
		*) refuse "git check-ignore failed" ;;
		esac
	fi

	# A tracked symlink checks out clean, so nothing else would show it (row 5b). A gitlink makes
	# `git status` run a child git in that folder, with that folder's config (AUTO-2, measured).
	git ls-tree -r -z "$2" >"$work/modes" || refuse "git ls-tree failed"
	tr '\0' '\n' <"$work/modes" | { grep -E '^(120000|160000) ' || true; } | cut -f2 >>"$work/hits"

	if [[ -s "$work/hits" ]]; then
		cat -v "$work/hits" >&2
		refuse "read these changes, then push with NW_ALLOW_CONTROL=1 if they are meant"
	fi
}

zero='^0+$'
while read -r local_ref local_sha remote_ref remote_sha; do
	if [[ "$local_sha" =~ $zero ]]; then continue; fi # deleting a remote branch
	if [[ "$remote_sha" =~ $zero ]]; then
		base=$(git merge-base "$main_sha" "$local_sha") || refuse "no merge base with main"
	else
		base=$remote_sha
	fi
	check_range "$base" "$local_sha"
done
