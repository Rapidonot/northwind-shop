# AUTO-1 evidence: failing-first log

Date: 2026-11-05 · Branch: `req/AUTO-1-gate-automation` · Base: `45707cc`

Every test passed on its first run, so none had been seen failing. To see each one fail, every guard in
`tools/ship.sh` and the `ship` and `review-diff` targets was removed in turn, and the tests that claim
that guard were run against the changed file. `killed` means they failed, as they should. File hashes
were compared before and after: both files were restored exactly.

## First pass: 46 mutants, 43 killed

```
killed      baseline label: tests/ship/test_ship.py::test_protected_change_without_label_is_refused[baseline]
killed      baseline label, quoted path: tests/ship/test_ship.py::test_quoted_path_is_refused[non-ascii]
killed      baseline label, large diff: tests/ship/test_ship.py::test_large_diff_with_the_protected_path_last_is_refused
killed      ci-change label: tests/ship/test_ship.py::test_workflow_change_without_label_is_refused[ci.yml]
killed      ci-change label, wrong label: tests/ship/test_ship.py::test_workflow_change_with_baseline_label_is_refused[ci.yml]
killed      --no-renames: tests/ship/test_ship.py::test_rename_out_of_baselines_is_refused
killed      nocasematch: tests/ship/test_ship.py::test_case_variant_is_refused[Evals/Baselines/x]
killed      nocasematch, workflows: tests/ship/test_ship.py::test_workflow_change_without_label_is_refused[case variant]
killed      NUL paths (-z): tests/ship/test_ship.py::test_quoted_path_is_refused[non-ascii]
killed      re-check labels after wait: tests/ship/test_ship.py::test_label_removed_mid_wait_is_refused
killed      exact label match: tests/ship/test_ship.py::test_lookalike_labels_are_refused[baseline-change-x]
killed      case-sensitive label: tests/ship/test_ship.py::test_lookalike_labels_are_refused[Baseline-Change]
killed      head moved: tests/ship/test_ship.py::test_head_moved_mid_wait_is_refused
killed      fetched head: tests/ship/test_ship.py::test_fetched_head_differing_from_head_ref_oid_is_refused
killed      bucket must pass: tests/ship/test_ship.py::test_a_job_that_did_not_pass_is_refused[python-fail]
killed      bucket must pass, watch 0: tests/ship/test_ship.py::test_a_failed_job_is_refused_even_when_watch_exits_zero
killed      bucket must pass, two entries: tests/ship/test_ship.py::test_two_entries_for_one_job_with_one_failing_is_refused
killed      job missing: tests/ship/test_ship.py::test_a_missing_job_is_refused[python]
killed      job missing, none at all: tests/ship/test_ship.py::test_no_required_jobs_at_all_is_refused
killed      workflow must be CI: tests/ship/test_ship.py::test_same_job_name_from_another_workflow_does_not_count[python]
killed      watch exit ignored: tests/ship/test_ship.py::test_all_passing_proceeds_even_when_watch_exits_one
killed      state OPEN: tests/ship/test_ship.py::test_pr_not_open_is_refused[CLOSED]
killed      base main: tests/ship/test_ship.py::test_base_other_than_main_is_refused[develop]
killed      draft: tests/ship/test_ship.py::test_draft_is_refused
killed      fork: tests/ship/test_ship.py::test_fork_is_refused
killed      gh version floor: tests/ship/test_ship.py::test_old_gh_is_refused[2.103.2]
killed      gh version parse: tests/ship/test_ship.py::test_unreadable_gh_version_is_refused
killed      checks help probe: tests/ship/test_ship.py::test_checks_help_lacking_a_feature_is_refused[--json]
killed      merge help probe: tests/ship/test_ship.py::test_merge_help_lacking_match_head_commit_is_refused
killed      MERGED check: tests/ship/test_ship.py::test_merge_success_but_final_state_open_does_not_runner_sync
killed      merge failure: tests/ship/test_ship.py::test_merge_failure_does_not_runner_sync
SURVIVED    --refmap= (rc 0): 1 passed, 151 deselected in 0.96s
killed      y/N prompt: tests/ship/test_ship.py::test_anything_but_y_does_not_merge[n\n]
killed      --match-head-commit: tests/ship/test_ship.py::test_clean_pr_merges_with_the_exact_argv_then_runner_syncs
killed      cat -v on list: tests/ship/test_ship.py::test_escape_character_in_a_path_prints_as_caret_bracket
killed      control surface: sync paths: tests/ship/test_ship.py::test_control_surface_lists_the_path[services/common/x.py]
killed      control surface: pattern paths: tests/ship/test_ship.py::test_control_surface_lists_the_path[.github/workflows/ci.yml]
killed      empty list: tests/ship/test_ship.py::test_a_pr_that_changes_no_files_is_refused
killed      single merge base: tests/ship/test_ship.py::test_more_than_one_merge_base_is_refused
SURVIVED    git diff error (rc 0): 1 passed, 151 deselected in 1.07s
killed      gh view error: tests/ship/test_ship.py::test_gh_pr_view_error_is_refused
killed      checks json error: tests/ship/test_ship.py::test_checks_json_error_is_refused
killed      make: clone guard: tests/ship/test_ship.py::test_refuses_outside_the_clone
SURVIVED    make: PR digits (rc 0): 6 passed, 146 deselected in 2.24s
killed      make: ship.sh in sync paths: tests/ship/test_ship.py::test_ship_script_is_in_runner_sync_paths
killed      make: review-diff dirty: tests/hooks/test_advisor_reviewer.py::test_review_diff_refuses_a_dirty_tree[modified]

46 mutants, 3 not killed
```

## The three survivors

- **`--refmap=`: a real test gap.** `origin/main` already matched the server, so a fetch that moved it
  changed nothing and the test could not fail. The test now moves main on the server first.
- **`git diff` error: a second guard caught it.** The first diff was disabled but the second one still
  refused. The mutant now removes every layer.
- **Make PR digits: a second guard caught it.** `ship.sh` checks its own argument as well. The mutant
  now removes both.

## Second pass: all three killed

```
killed      --refmap= dropped: tests/ship/test_ship.py::test_origin_main_in_the_clone_is_unmoved_when_gh_merges
killed      git diff error, all layers: tests/ship/test_ship.py::test_git_diff_error_is_refused
killed      PR digits, both layers: tests/ship/test_ship.py::test_refuses_a_bad_pr_value[abc]
```

The mutation scripts were not committed. They replaced exact strings in the two files, ran the named
tests with `-k`, and restored the files.

## Tests added because of this run

- `test_a_pr_that_changes_no_files_is_refused` and `test_more_than_one_merge_base_is_refused`: both
  guards had no test.
- `test_origin_main_in_the_clone_is_unmoved_when_gh_merges`: rewritten, as above. This strengthens it.

## What this does not show

The fake `gh` proves how `ship.sh` reacts. It does not prove what real `gh` returns. The `gh pr checks`
behaviour relied on was read from the `gh` source on 2026-11-05, not run. `assert_refused` does not check
the reason for a refusal; this log is what ties each refusal to its guard.

## Third pass: the final code, after the security review

The review changed all four pieces, so the two passes above no longer describe the code. This pass covers
`tools/ship.sh`, `tools/refused-paths.sh`, `tools/pre-push.proposed.sh` and the Makefile, with every
layer of a layered guard removed together: 100 mutants, 88 killed, 12 not.

```
killed      baseline label block: tests/ship/test_ship.py::test_protected_change_without_label_is_refused[baseline]
killed      baseline block, quoted path: tests/ship/test_ship.py::test_quoted_path_is_refused[non-ascii]
killed      baseline block, large diff: tests/ship/test_ship.py::test_large_diff_with_the_protected_path_last_is_refused
killed      ci block: tests/ship/test_ship.py::test_workflow_change_without_label_is_refused[ci.yml]
killed      ci block, wrong label: tests/ship/test_ship.py::test_workflow_change_with_baseline_label_is_refused[ci.yml]
killed      label exact match, both layers: tests/ship/test_ship.py::test_lookalike_labels_are_refused[baseline-change-x]
killed      actor must be me: tests/ship/test_ship.py::test_label_not_added_by_the_user_is_refused[bot-baseline-change]
killed      actor type User: tests/ship/test_ship.py::test_label_not_added_by_the_user_is_refused[user-with-type-bot-baseline-change]
killed      events sorted: tests/ship/test_ship.py::test_label_not_added_by_the_user_is_refused[bot-listed-first-but-latest-baseline-change]
SURVIVED    login shape (rc 0): 2 passed, 468 deselected in 1.58s
killed      gh api user error: tests/ship/test_ship.py::test_a_failing_actor_lookup_is_refused[user_fail-baseline-change]
killed      events error: tests/ship/test_ship.py::test_a_failing_actor_lookup_is_refused[events_fail-baseline-change]
killed      re-check labels, removal: tests/ship/test_ship.py::test_label_removed_mid_wait_is_refused
killed      re-check labels, bot: tests/ship/test_ship.py::test_label_re_added_by_a_bot_during_the_wait_is_refused[baseline-change]
killed      --no-renames: tests/ship/test_ship.py::test_rename_out_of_baselines_is_refused
killed      nocasematch: tests/ship/test_ship.py::test_case_variant_is_refused[Evals/Baselines/x]
killed      NUL paths: tests/ship/test_ship.py::test_quoted_path_is_refused[non-ascii]
killed      icase pathspecs: tests/ship/test_ship.py::test_sync_paths_match_case_insensitively_in_ship
killed      refused-paths call: tests/ship/test_ship.py::test_ship_refuses_the_path[GNUmakefile]
SURVIVED    empty list (rc 5): 470 deselected in 0.05s
SURVIVED    single merge base (rc 5): 470 deselected in 0.05s
SURVIVED    git diff error, all layers (rc 0): 1 passed, 469 deselected in 1.07s
SURVIVED    --refmap= (rc 0): 1 passed, 469 deselected in 3.64s
killed      fetched head: tests/ship/test_ship.py::test_fetched_head_differing_from_head_ref_oid_is_refused
SURVIVED    reviewed ancestor (rc 0): 3 passed, 467 deselected in 2.02s
killed      reviewed later commit: tests/ship/test_ship.py::test_a_later_commit_touching_code_is_refused_with_its_path
killed      reviewed docs exception: tests/ship/test_ship.py::test_a_later_commit_touching_only_the_progress_files_merges[progress]
killed      every check passes, job: tests/ship/test_ship.py::test_a_job_that_did_not_pass_is_refused[python-fail]
killed      every check passes, watch 0: tests/ship/test_ship.py::test_a_failed_job_is_refused_even_when_watch_exits_zero
killed      every check passes, two entries: tests/ship/test_ship.py::test_two_entries_for_one_job_with_one_failing_is_refused
killed      every check passes, other check: tests/ship/test_ship.py::test_any_other_check_that_did_not_pass_is_refused[CI-fail]
killed      job missing: tests/ship/test_ship.py::test_a_missing_job_is_refused[python]
killed      workflow must be CI: tests/ship/test_ship.py::test_same_job_name_from_another_workflow_does_not_count[python]
killed      watch exit ignored: tests/ship/test_ship.py::test_all_passing_proceeds_even_when_watch_exits_one
killed      checks json error: tests/ship/test_ship.py::test_checks_json_error_is_refused
killed      state OPEN: tests/ship/test_ship.py::test_pr_not_open_is_refused[CLOSED]
killed      base main: tests/ship/test_ship.py::test_base_other_than_main_is_refused[develop]
killed      draft: tests/ship/test_ship.py::test_draft_is_refused
killed      fork: tests/ship/test_ship.py::test_fork_is_refused
killed      head moved: tests/ship/test_ship.py::test_head_moved_mid_wait_is_refused
killed      gh view error: tests/ship/test_ship.py::test_gh_pr_view_error_is_refused
killed      gh version floor: tests/ship/test_ship.py::test_old_gh_is_refused[2.103.2]
killed      gh version parse: tests/ship/test_ship.py::test_unreadable_gh_version_is_refused
killed      checks help probe: tests/ship/test_ship.py::test_checks_help_lacking_a_feature_is_refused[--json]
killed      merge help probe: tests/ship/test_ship.py::test_merge_help_lacking_match_head_commit_is_refused
killed      typed when surface: tests/ship/test_ship.py::test_surface_change_asks_for_the_pr_number_and_y_refuses
killed      typed number compared: tests/ship/test_ship.py::test_a_wrong_number_refuses[8]
killed      y compared: tests/ship/test_ship.py::test_anything_but_y_does_not_merge[n]
killed      type-ahead dropped: tests/ship/test_ship.py::test_typeahead_is_discarded[False]
killed      --match-head-commit: tests/ship/test_ship.py::test_clean_pr_merges_with_the_exact_argv_then_runner_syncs
killed      merge failure: tests/ship/test_ship.py::test_a_failed_merge_with_an_escape_still_refuses
killed      merge output escaped: tests/ship/test_ship.py::test_merge_output_with_an_escape_prints_as_caret_bracket
killed      MERGED check: tests/ship/test_ship.py::test_merge_success_but_final_state_open_does_not_runner_sync
killed      list escaped: tests/ship/test_ship.py::test_escape_character_in_a_path_prints_as_caret_bracket
killed      newline escaped: tests/ship/test_ship.py::test_a_newline_in_a_path_cannot_forge_a_none_line
killed      sync diff shown: tests/ship/test_ship.py::test_the_full_diff_of_sync_paths_is_shown_escaped_before_the_block
killed      sync diff escaped: tests/ship/test_ship.py::test_the_full_diff_of_sync_paths_is_shown_escaped_before_the_block
killed      surface: sync paths: tests/ship/test_ship.py::test_control_surface_lists_the_path[services/common/x.py]
killed      surface: evals: tests/ship/test_ship.py::test_control_surface_lists_the_path[evals/baselines/a.json]
killed      surface: cassettes, conftest, gitignore: tests/ship/test_ship.py::test_control_surface_lists_the_path[cassettes/a/b.json]
killed      surface: pyproject, package: tests/ship/test_ship.py::test_control_surface_lists_the_path[package.json]
killed      ref: make variants: tests/ship/test_ship.py::test_ship_refuses_the_path[GNUmakefile]
killed      ref: make variants, runner-sync: tests/ship/test_ship.py::test_runner_sync_refuses_the_path_before_asking[GNUmakefile]
killed      ref: dot dirs: tests/ship/test_ship.py::test_ship_refuses_the_path[.env]
killed      ref: dot dirs, runner-sync: tests/ship/test_ship.py::test_runner_sync_refuses_the_path_before_asking[.env]
killed      ref: Makefile allowed: tests/ship/test_ship.py::test_ship_does_not_refuse_the_path[Makefile]
killed      ref: hits refuse: tests/ship/test_ship.py::test_ship_refuses_the_path[GNUmakefile]
killed      ref: ignored refuse: tests/ship/test_ship.py::test_ship_refuses_a_path_the_checked_out_gitignore_ignores[ignored-dir/x]
SURVIVED    ref: --no-index (rc 0): 9 passed, 461 deselected in 6.61s
SURVIVED    ref: check-ignore error (rc 5): 470 deselected in 0.05s
killed      ref: case fold: tests/ship/test_ship.py::test_ship_refuses_paths_that_differ_only_by_case
SURVIVED    ref: diff error (rc 5): 470 deselected in 0.06s
SURVIVED    ref: ls-tree error (rc 5): 470 deselected in 0.06s
killed      push: override only 1: tests/ship/test_pre_push.py::test_any_other_value_does_not_override[direct-0]
killed      push: .github: tests/ship/test_pre_push.py::test_refuses_the_path_and_names_it[direct-.github/workflows/ci.yml]
killed      push: make variants: tests/ship/test_pre_push.py::test_refuses_the_path_and_names_it[direct-GNUmakefile]
killed      push: dot dirs: tests/ship/test_pre_push.py::test_refuses_the_path_and_names_it[direct-.env]
SURVIVED    push: Makefile allowed (rc 0): 8 passed, 462 deselected in 5.14s
killed      push: main's gitignore: tests/ship/test_pre_push.py::test_a_worktree_gitignore_cannot_unignore_a_path[direct]
killed      push: gitignore fail closed: tests/ship/test_pre_push.py::test_origin_main_missing_is_refused[direct-True]
killed      push: ignored counted: tests/ship/test_pre_push.py::test_refuses_a_path_main_ignores[direct-a.log]
killed      push: check-ignore in empty repo: tests/ship/test_pre_push.py::test_a_worktree_gitignore_cannot_unignore_a_path[direct]
SURVIVED    push: check-ignore error (rc 5): 470 deselected in 0.06s
killed      push: hits refuse: tests/ship/test_pre_push.py::test_refuses_the_path_and_names_it[direct-GNUmakefile]
killed      push: new branch base: tests/ship/test_pre_push.py::test_a_new_branch_is_diffed_against_origin_main[direct]
killed      push: remote sha base: tests/ship/test_pre_push.py::test_a_control_path_already_on_the_remote_branch_does_not_refuse_a_clean_push[direct]
killed      push: delete skip: tests/ship/test_pre_push.py::test_deleting_a_remote_branch_passes[direct]
killed      push: unknown remote sha: tests/ship/test_pre_push.py::test_a_remote_sha_unknown_locally_is_refused
killed      push: path escaped: tests/ship/test_pre_push.py::test_an_escape_in_a_path_prints_as_caret_bracket[direct]
killed      make: clone guard: tests/ship/test_ship.py::test_refuses_outside_the_clone
killed      make: PR and SHA shape, both layers: tests/ship/test_ship.py::test_make_refuses_a_bad_sha[empty]
killed      make: pin PR: tests/ship/test_ship.py::test_make_does_not_expand_the_arguments[pr-shell]
killed      make: pin SHA: tests/ship/test_ship.py::test_make_does_not_expand_the_arguments[sha-shell]
killed      make: window, ship: tests/ship/test_ship.py::test_ship_refuses_while_a_window_is_open
killed      make: window, runner-sync: tests/ship/test_ship.py::test_runner_sync_refuses_while_a_window_is_open
killed      make: refused in runner-sync: tests/ship/test_ship.py::test_runner_sync_refuses_the_path_before_asking[GNUmakefile]
killed      make: icase in runner-sync: tests/ship/test_ship.py::test_runner_sync_shows_the_sync_paths_case_insensitively
killed      make: ship.sh in sync paths: tests/ship/test_ship.py::test_script_is_in_runner_sync_paths[tools/ship.sh]
killed      make: refused-paths in sync paths: tests/ship/test_ship.py::test_script_is_in_runner_sync_paths[tools/refused-paths.sh]
killed      make: review-diff dirty: tests/hooks/test_advisor_reviewer.py::test_review_diff_refuses_a_dirty_tree[modified]

done, 12 not killed
```

### The twelve survivors

- **Tests lost in a rewrite (3).** `test-writer` rewrote `tests/ship/test_ship.py` and dropped
  `test_a_pr_that_changes_no_files_is_refused` and `test_more_than_one_merge_base_is_refused`, and undid
  the `--refmap=` strengthening, although it reported all earlier tests kept. All three are restored.
- **Real gaps (7).** `--no-index`, the `check-ignore`, `git diff` and `ls-tree` errors in
  `refused-paths.sh`, the `check-ignore` error and the allowed paths in the pre-push hook, and the ancestor
  check, which a same-tree twin commit reaches alone. Each now has a test.
- **Hidden by a second layer (2).** The login shape check, behind the `User` check, and the `git diff`
  error, behind later diffs. A new test reaches the first; the mutant now removes every layer of the second.

One rerun of these was discarded: a collection error from a misplaced decorator also exits 1, and the
harness counted that as a kill. The harness now requires a `FAILED` line. The 88 kills in the first run
were checked and each names a failed test.

### Rerun of the twelve: all killed

```
killed      login shape: tests/ship/test_ship.py::test_an_unusual_login_is_refused_even_when_the_actor_matches_it
killed      empty list: tests/ship/test_ship.py::test_a_pr_that_changes_no_files_is_refused
killed      single merge base: tests/ship/test_ship.py::test_more_than_one_merge_base_is_refused
killed      git diff error, all layers: tests/ship/test_ship.py::test_git_diff_error_is_refused
killed      --refmap=: tests/ship/test_ship.py::test_origin_main_in_the_clone_is_unmoved_when_gh_merges
killed      reviewed ancestor: tests/ship/test_ship.py::test_a_twin_commit_with_the_same_tree_is_not_the_reviewed_commit
killed      ref: --no-index: tests/ship/test_refused_paths.py::test_a_tracked_path_the_gitignore_ignores_is_refused
killed      ref: check-ignore error: tests/ship/test_refused_paths.py::test_check_ignore_error_is_refused
killed      ref: diff error: tests/ship/test_refused_paths.py::test_refused_paths_error_on_a_bad_revision
killed      ref: ls-tree error: tests/ship/test_refused_paths.py::test_refused_paths_error_on_ls_tree
killed      push: Makefile allowed: tests/ship/test_pre_push.py::test_push_does_not_refuse_the_path[direct-Makefile]
killed      push: check-ignore error: tests/ship/test_pre_push.py::test_check_ignore_error_is_refused

done, 0 not killed
```

File hashes were compared after every run: all four files were restored exactly.

## Fourth pass: after the re-review fixes

The scoped re-review found two highs. Both were fixed and the code changed again, so this is the pass that
describes the code being committed: 113 mutants, 110 killed, 3 not.

```
killed      baseline label block: tests/ship/test_ship.py::test_protected_change_without_label_is_refused[baseline]
killed      baseline block, quoted path: tests/ship/test_ship.py::test_quoted_path_is_refused[double-quote]
killed      baseline block, large diff: tests/ship/test_ship.py::test_large_diff_with_the_protected_path_last_is_refused
killed      ci block: tests/ship/test_ship.py::test_workflow_change_without_label_is_refused[ci.yml]
killed      ci block, wrong label: tests/ship/test_ship.py::test_workflow_change_with_baseline_label_is_refused[ci.yml]
killed      label exact match, both layers: tests/ship/test_ship.py::test_lookalike_labels_are_refused[baseline-change-x]
killed      actor must be me: tests/ship/test_ship.py::test_label_not_added_by_the_user_is_refused[bot-baseline-change]
killed      actor type User: tests/ship/test_ship.py::test_label_not_added_by_the_user_is_refused[user-with-type-bot-baseline-change]
killed      events sorted: tests/ship/test_ship.py::test_label_not_added_by_the_user_is_refused[bot-listed-first-but-latest-baseline-change]
killed      login shape: tests/ship/test_ship.py::test_an_unusual_login_is_refused_even_when_the_actor_matches_it
killed      gh api user error: tests/ship/test_ship.py::test_a_failing_actor_lookup_is_refused[user_fail-baseline-change]
killed      events error: tests/ship/test_ship.py::test_a_failing_actor_lookup_is_refused[events_fail-baseline-change]
killed      re-check labels, removal: tests/ship/test_ship.py::test_label_removed_mid_wait_is_refused
killed      re-check labels, bot: tests/ship/test_ship.py::test_label_re_added_by_a_bot_during_the_wait_is_refused[baseline-change]
killed      --no-renames: tests/ship/test_ship.py::test_rename_out_of_baselines_is_refused
killed      nocasematch: tests/ship/test_ship.py::test_case_variant_is_refused[Evals/Baselines/x]
killed      NUL paths: tests/ship/test_ship.py::test_quoted_path_is_refused[double-quote]
killed      icase pathspecs: tests/ship/test_ship.py::test_sync_paths_match_case_insensitively_in_ship
killed      refused-paths call: tests/ship/test_ship.py::test_ship_refuses_the_path[GNUmakefile]
killed      empty list: tests/ship/test_ship.py::test_a_pr_that_changes_no_files_is_refused
killed      single merge base: tests/ship/test_ship.py::test_more_than_one_merge_base_is_refused
killed      git diff error, all layers: tests/ship/test_ship.py::test_git_diff_error_is_refused
killed      --refmap=: tests/ship/test_ship.py::test_origin_main_in_the_clone_is_unmoved_when_gh_merges
killed      fetched head: tests/ship/test_ship.py::test_fetched_head_differing_from_head_ref_oid_is_refused
killed      reviewed ancestor: tests/ship/test_ship.py::test_a_twin_commit_with_the_same_tree_is_not_the_reviewed_commit
killed      reviewed later commit: tests/ship/test_ship.py::test_a_later_commit_touching_code_is_refused_with_its_path
killed      reviewed docs exception: tests/ship/test_ship.py::test_a_later_commit_touching_only_the_progress_files_merges[progress]
killed      every check passes, job: tests/ship/test_ship.py::test_a_job_that_did_not_pass_is_refused[python-fail]
killed      every check passes, watch 0: tests/ship/test_ship.py::test_a_failed_job_is_refused_even_when_watch_exits_zero
killed      every check passes, two entries: tests/ship/test_ship.py::test_two_entries_for_one_job_with_one_failing_is_refused
killed      every check passes, other check: tests/ship/test_ship.py::test_any_other_check_that_did_not_pass_is_refused[CI-fail]
killed      job missing: tests/ship/test_ship.py::test_a_missing_job_is_refused[python]
killed      workflow must be CI: tests/ship/test_ship.py::test_same_job_name_from_another_workflow_does_not_count[python]
killed      watch exit ignored: tests/ship/test_ship.py::test_all_passing_proceeds_even_when_watch_exits_one
killed      checks json error: tests/ship/test_ship.py::test_checks_json_error_is_refused
killed      state OPEN: tests/ship/test_ship.py::test_pr_not_open_is_refused[CLOSED]
killed      base main: tests/ship/test_ship.py::test_base_other_than_main_is_refused[develop]
killed      draft: tests/ship/test_ship.py::test_draft_is_refused
killed      fork: tests/ship/test_ship.py::test_fork_is_refused
killed      head moved: tests/ship/test_ship.py::test_head_moved_mid_wait_is_refused
killed      gh view error: tests/ship/test_ship.py::test_gh_pr_view_error_is_refused
killed      gh version floor: tests/ship/test_ship.py::test_old_gh_is_refused[2.103.2]
killed      gh version parse: tests/ship/test_ship.py::test_unreadable_gh_version_is_refused
killed      checks help probe: tests/ship/test_ship.py::test_checks_help_lacking_a_feature_is_refused[--json]
killed      merge help probe: tests/ship/test_ship.py::test_merge_help_lacking_match_head_commit_is_refused
killed      typed when surface: tests/ship/test_ship.py::test_surface_change_asks_for_the_pr_number_and_y_refuses
killed      typed number compared: tests/ship/test_ship.py::test_a_wrong_number_refuses[8]
killed      y compared: tests/ship/test_ship.py::test_anything_but_y_does_not_merge[n]
killed      type-ahead dropped: tests/ship/test_ship.py::test_typeahead_is_discarded[False]
killed      --match-head-commit: tests/ship/test_ship.py::test_clean_pr_merges_with_the_exact_argv_then_runner_syncs
killed      merge failure: tests/ship/test_ship.py::test_a_failed_merge_with_an_escape_still_refuses
killed      merge output escaped: tests/ship/test_ship.py::test_merge_output_with_an_escape_prints_as_caret_bracket
killed      MERGED check: tests/ship/test_ship.py::test_merge_success_but_final_state_open_does_not_runner_sync
killed      list escaped: tests/ship/test_ship.py::test_escape_character_in_a_path_prints_as_caret_bracket
killed      newline escaped: tests/ship/test_ship.py::test_a_newline_in_a_path_cannot_forge_a_none_line
killed      sync diff shown: tests/ship/test_ship.py::test_the_full_diff_of_sync_paths_is_shown_escaped_before_the_block
killed      label-gated diff shown: tests/ship/test_ship.py::test_the_full_diff_of_label_gated_paths_is_shown[evals/baselines/a.json-baseline-change]
killed      sync diff escaped: tests/ship/test_ship.py::test_the_full_diff_of_sync_paths_is_shown_escaped_before_the_block
killed      surface: sync paths: tests/ship/test_ship.py::test_control_surface_lists_the_path[services/common/x.py]
killed      surface: evals: tests/ship/test_ship.py::test_control_surface_lists_the_path[evals/baselines/a.json]
killed      surface: cassettes, conftest, gitignore: tests/ship/test_ship.py::test_control_surface_lists_the_path[cassettes/a/b.json]
killed      surface: pyproject, package: tests/ship/test_ship.py::test_control_surface_lists_the_path[package.json]
killed      ref: printable ASCII: tests/ship/test_refused_paths.py::test_a_path_outside_printable_ascii_is_refused[\u017fervices/common/brotli.py]
SURVIVED    ref: C locale (rc 0): 15 passed, 500 deselected in 6.07s
killed      ref: make variants: tests/ship/test_ship.py::test_ship_refuses_the_path[GNUmakefile]
killed      ref: make variants, runner-sync: tests/ship/test_ship.py::test_runner_sync_refuses_the_path_before_asking[GNUmakefile]
killed      ref: dot dirs: tests/ship/test_ship.py::test_ship_refuses_the_path[.env]
killed      ref: dot dirs, runner-sync: tests/ship/test_ship.py::test_runner_sync_refuses_the_path_before_asking[.env]
killed      ref: Makefile allowed: tests/ship/test_ship.py::test_ship_does_not_refuse_the_path[Makefile]
killed      ref: hits refuse: tests/ship/test_ship.py::test_ship_refuses_the_path[GNUmakefile]
killed      ref: ignored refuse: tests/ship/test_ship.py::test_ship_refuses_a_path_the_checked_out_gitignore_ignores[ignored-dir/x]
killed      ref: --no-index: tests/ship/test_refused_paths.py::test_a_tracked_path_the_gitignore_ignores_is_refused
killed      ref: check-ignore error: tests/ship/test_refused_paths.py::test_check_ignore_error_is_refused
killed      ref: case fold: tests/ship/test_ship.py::test_ship_refuses_paths_that_differ_only_by_case
killed      ref: diff error: tests/ship/test_refused_paths.py::test_refused_paths_error_on_a_bad_revision
killed      ref: ls-tree error: tests/ship/test_refused_paths.py::test_refused_paths_error_on_ls_tree
killed      push: override only 1: tests/ship/test_pre_push.py::test_any_other_value_does_not_override[direct-0]
killed      push: .github: tests/ship/test_pre_push.py::test_refuses_the_path_and_names_it[direct-.github/workflows/ci.yml]
killed      push: make variants: tests/ship/test_pre_push.py::test_refuses_the_path_and_names_it[direct-GNUmakefile]
killed      push: dot dirs: tests/ship/test_pre_push.py::test_refuses_the_path_and_names_it[direct-.env]
killed      push: Makefile allowed: tests/ship/test_pre_push.py::test_push_does_not_refuse_the_path[direct-Makefile]
killed      push: main's gitignore: tests/ship/test_pre_push.py::test_a_worktree_gitignore_cannot_unignore_a_path[direct]
killed      push: main from the server, branch: tests/ship/test_pre_push.py::test_a_moved_origin_main_cannot_hide_a_new_branch[direct]
killed      push: main from the server, gitignore: tests/ship/test_pre_push.py::test_a_moved_origin_main_cannot_supply_the_gitignore[direct]
killed      push: shadowing name: tests/ship/test_pre_push.py::test_a_shadowing_tag_cannot_stand_in_for_main[direct]
killed      push: origin has main: tests/ship/test_pre_push.py::test_origin_without_main_is_refused
killed      push: main known locally: tests/ship/test_pre_push.py::test_a_main_not_fetched_yet_is_refused
killed      push: replace refs off: tests/ship/test_pre_push.py::test_a_replace_ref_cannot_show_a_clean_tree
SURVIVED    push: grafts off (rc 0): 1 passed, 514 deselected in 0.92s
killed      push: shallow: tests/ship/test_pre_push.py::test_a_shallow_clone_is_refused
killed      push: printable ASCII: tests/ship/test_pre_push.py::test_refuses_a_path_outside_printable_ascii[direct-\u017fervices/common/brotli.py]
killed      push: gitignore fail closed: tests/ship/test_pre_push.py::test_gitignore_absent_on_origin_main_is_refused[direct]
killed      push: ignored counted: tests/ship/test_pre_push.py::test_refuses_a_path_main_ignores[direct-a.log]
BAD-MUTANT  push: check-ignore in empty repo: pattern found 0 times: '\t\t\tcd "$work/ignore"\n'
killed      push: check-ignore error: tests/ship/test_pre_push.py::test_check_ignore_error_is_refused
killed      push: hits refuse: tests/ship/test_pre_push.py::test_refuses_the_path_and_names_it[direct-GNUmakefile]
killed      push: new branch base: tests/ship/test_pre_push.py::test_a_new_branch_is_diffed_against_origin_main[direct]
killed      push: remote sha base: tests/ship/test_pre_push.py::test_a_control_path_already_on_the_remote_branch_does_not_refuse_a_clean_push[direct]
killed      push: delete skip: tests/ship/test_pre_push.py::test_deleting_a_remote_branch_passes[direct]
killed      push: unknown remote sha: tests/ship/test_pre_push.py::test_a_remote_sha_unknown_locally_is_refused
killed      push: path escaped: tests/ship/test_pre_push.py::test_an_escape_in_a_path_prints_as_caret_bracket[direct]
killed      make: clone guard: tests/ship/test_ship.py::test_refuses_outside_the_clone
killed      make: PR and SHA shape, both layers: tests/ship/test_ship.py::test_make_refuses_a_bad_sha[empty]
killed      make: pin PR: tests/ship/test_ship.py::test_make_does_not_expand_the_arguments[pr-shell]
killed      make: pin SHA: tests/ship/test_ship.py::test_make_does_not_expand_the_arguments[sha-shell]
killed      make: window, ship: tests/ship/test_ship.py::test_ship_refuses_while_a_window_is_open
killed      make: window, runner-sync: tests/ship/test_ship.py::test_runner_sync_refuses_while_a_window_is_open
killed      make: refused in runner-sync: tests/ship/test_ship.py::test_runner_sync_refuses_the_path_before_asking[GNUmakefile]
killed      make: icase in runner-sync: tests/ship/test_ship.py::test_runner_sync_shows_the_sync_paths_case_insensitively
killed      make: ship.sh in sync paths: tests/ship/test_ship.py::test_script_is_in_runner_sync_paths[tools/ship.sh]
killed      make: refused-paths in sync paths: tests/ship/test_ship.py::test_script_is_in_runner_sync_paths[tools/refused-paths.sh]
killed      make: gitignore in sync paths: tests/ship/test_ship.py::test_the_gitignore_that_drives_a_refusal_is_a_sync_path[ .gitignore ]
killed      make: review-diff dirty: tests/hooks/test_advisor_reviewer.py::test_review_diff_refuses_a_dirty_tree[modified]

done, 3 not killed
```

### The three left

- **`ref: C locale`: the test could not fail.** The test env has no `LANG`, so removing `export LC_ALL=C`
  changed nothing. In a UTF-8 terminal the non-ASCII refusal then passes U+017F. New tests run under
  `en_US.UTF-8`, and removing the line now fails them, in both scripts.
- **`push: grafts off`: the test used the wrong attack.** It now grafts main onto the pushed commit, which
  is what empties the diff.
- **`push: check-ignore in empty repo`: a stale pattern in the harness**, after the `|| exit 128` edit.

### Rerun: all killed, with one mutant added for the hook's locale

```
killed      ref: C locale: tests/ship/test_refused_paths.py::test_a_utf8_terminal_does_not_let_a_non_ascii_path_through[\u017fervices/common/brotli.py]
killed      push: C locale: tests/ship/test_pre_push.py::test_a_utf8_terminal_does_not_let_a_non_ascii_path_through[\u017fervices/common/brotli.py]
killed      push: grafts off: tests/ship/test_pre_push.py::test_a_graft_cannot_hide_a_commit
killed      push: check-ignore in empty repo: tests/ship/test_pre_push.py::test_a_worktree_gitignore_cannot_unignore_a_path[direct]

done, 0 not killed
```

Total for the final code: 114 mutants, all killed by a named failing test. The `killed` lines in the first
two passes are history: they describe earlier code.

## Measured during review

- A committed `GNUmakefile` ran instead of the reviewed `Makefile` (GNU make 3.81).
- `git merge --ff-only` overwrote an ignored file with a tracked one.
- On this APFS volume `ls d/services` shows a file created under a name spelled with U+017F, and
  `git merge --ff-only` wrote `evil.py` into `services/` from such a path.
- `PR='$(shell touch expanded)'` created the file before any guard ran, until `PR` and `SHA` were pinned.
- Neither the terminal temp directory nor `/tmp` is writable from the sandbox, so finding 4 did not apply.

## Advisor review and triage

`advisor-reviewer` read `make review-diff` at base `45707cc`, head `4fd3d77`, and returned `findings`: no
critical or high, three medium, three low. The user's triage is that none blocks this row.

| # | Severity | Finding | Triage |
|---|---|---|---|
| 1 | Medium | 12 refusals have no test and no mutant: 9 in `tools/ship.sh`, 3 in `tools/pre-push.proposed.sh` | Accepted for this row. AUTO-2 adds them, at least the `ship.sh` fetch, merge-base and post-merge confirmation, and the pre-push `ls-remote` and merge-base |
| 2 | Medium | Decision F2's "those same paths" was read without `.github/**`, and the reading was not recorded | Intended reading, now in decision 0008. F2's refusals are paths the runner loads; `.github/**` is refused at push by the hook; in `ship`, workflows are label-gated, and the other `.github` paths join the control surface in AUTO-2 |
| 3 | Medium | The fixes for the re-review's two highs were never reviewed | The user's advisor read `tools/pre-push.proposed.sh`, `tools/refused-paths.sh`, `tools/ship.sh` and the `Makefile` changes at `5840f97`, and accepted the F1 and F2 fixes in place of a second `security-reviewer` pass. Recorded from the user's triage; the subagent did not give this acceptance |
| 4 | Low | Failing-first was replaced by mutation, with no recorded approval | Accepted: each mutant is the guard removed, which is failing-first. AUTO-2 commits the mutation script so it can be rerun |
| 5 | Low | Two existing tests were edited without being logged | Logged below |
| 6 | Low | Records are stale at the reviewed head | `/req-done` corrects them |

The review also left items `unconfirmed`, including what local fixes 5, 11, 14 and 15 were and whether the
`runner-sync` change went to the re-review as a separate diff. The triage did not cover those, and this
section does not settle them.

### Existing tests edited in this row

Neither edit weakens a check.

- `evals/tests/test_rag12_followup.py`, `test_the_pins_come_before_the_first_shell_call`. Pinning `PR` and
  `SHA` broke its count, `len(pins) == len(PROMOTE_FLAGS)`. It now requires the exact set of pinned names,
  `PROMOTE_FLAGS` plus `PR` and `SHA`, which is stricter. The ordering assertion is unchanged.
- `services/llm-replay/tests/test_runner_support.py`, `rig()` only. It copies `tools/refused-paths.sh`,
  creates a `tmp` directory, and sets `TMPDIR` and `LLM_RUNNER_PORT=1`, which the new `runner-sync` needs. No
  assertion changed. This file is in `RUNNER_SYNC_PATHS`, so it is on the control surface.
