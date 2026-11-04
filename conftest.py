"""Repo-root pytest settings.

Collection stats every entry in the root directory, and Claude's sandbox denies the key file (hard
rule 4, decision 0004), so a run in the main checkout would stop before any test. Ignored paths are
never stat'ed, so pytest stays away from the file instead of needing access to it.
"""

collect_ignore_glob = [".env*"]
