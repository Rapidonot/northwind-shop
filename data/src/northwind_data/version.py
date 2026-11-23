"""Dataset identity (FOUND-1).

Bump DATA_VERSION whenever the generated output changes. `make data` refuses to overwrite
fixtures whose content changed under the same version, because every cassette recorded against
the old corpus becomes a replay miss (decision 0001).
"""

from datetime import date

DATA_VERSION = 3  # row #32: R01222 to R01224 appended
DEFAULT_SEED = 20261104
# Every date in the dataset is an offset from this; nothing reads the clock.
BASE_DATE = date(2026, 10, 1)
