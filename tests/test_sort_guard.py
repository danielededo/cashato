"""The /transactions sort whitelist is interpolated straight into ORDER BY.

That is safe ONLY because the value never comes from the request: the request
carries a KEY, and the SQL fragment comes from this dict. This test pins both
halves of that contract — the mapping stays free of anything a f-string could
turn into injection, and `abs_amount` (what the Review queue orders by, so the
user's attention goes to the rows that move the totals) stays available.

No DB: the whitelist is data, and the routes module carries no import-time
side effects (metrics/tracing live in app.py, which this test avoids).
"""

import re

from cashato.services.query_api.routes import SORT_COLS as _SORT_COLS

# A column name, or a single call over one column: `abs(amount)`. Nothing that
# could carry a subquery, a semicolon, a comment or a second expression.
_SAFE = re.compile(r"^[a-z_]+(?:\([a-z_]+\))?$")


def test_every_sort_fragment_is_a_bare_column_or_single_call() -> None:
    for key, fragment in _SORT_COLS.items():
        assert _SAFE.fullmatch(fragment), f"{key!r} maps to unsafe SQL: {fragment!r}"


def test_review_queue_can_order_by_magnitude() -> None:
    # Signed `amount` would bunch every inflow at one end; the review queue
    # needs the biggest movements regardless of direction.
    assert _SORT_COLS["abs_amount"] == "abs(amount)"
