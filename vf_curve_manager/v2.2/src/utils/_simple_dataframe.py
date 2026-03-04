"""
Minimal DataFrame-like container — pure stdlib, zero external dependencies.

Replaces the pandas.DataFrame usage inside this tool:
  - Construction from a column dict
  - .columns          → list of column names
  - .values           → rows object with .tolist()
  - .iloc[:, n]       → column-access object with .values (list)
  - SimpleDataFrame(data) where data = {col: [values...], ...}

No data ever leaves the machine.  No third-party code involved.
"""

from __future__ import annotations
from typing import Dict, List, Any, Iterator


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _ColArray:
    """A single column's data — mimics numpy array surface used here."""

    __slots__ = ('values',)

    def __init__(self, data: List[Any]) -> None:
        self.values: List[Any] = list(data)

    def __iter__(self) -> Iterator[Any]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def __getitem__(self, key: Any) -> Any:
        return self.values[key]


class _ILocIndexer:
    """Supports ``df.iloc[:, n]`` column access."""

    __slots__ = ('_rows', '_ncols')

    def __init__(self, rows: List[List[Any]], ncols: int) -> None:
        self._rows = rows
        self._ncols = ncols

    def __getitem__(self, key: Any) -> _ColArray:
        # Accept (slice_or_int, int) — only column-slice form is used here.
        if isinstance(key, tuple) and len(key) == 2:
            _, col_idx = key
        else:
            col_idx = key
        return _ColArray([row[col_idx] for row in self._rows])


class _RowsView:
    """Mimics ``df.values`` — supports ``.tolist()``."""

    __slots__ = ('_rows',)

    def __init__(self, rows: List[List[Any]]) -> None:
        self._rows = rows

    def tolist(self) -> List[List[Any]]:
        return [list(r) for r in self._rows]

    def __iter__(self) -> Iterator[List[Any]]:
        return iter(self._rows)


class _ColumnsList(list):  # type: ignore[type-arg]
    """Mimics ``df.columns`` — a list with a ``.tolist()`` method."""

    def tolist(self) -> List[str]:
        return list(self)


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class SimpleDataFrame:
    """
    Lightweight, stdlib-only drop-in for the pandas.DataFrame API subset
    used in this tool.

    Usage::

        df = SimpleDataFrame({'WP': ['P0','P1'], 'Voltage': [0.8, 0.9], 'Freq': [800, 1000]})
        df.columns.tolist()       # ['WP', 'Voltage', 'Freq']
        df.values.tolist()        # [['P0', 0.8, 800], ['P1', 0.9, 1000]]
        df.iloc[:, 1].values      # [0.8, 0.9]
    """

    def __init__(self, data: Dict[str, List[Any]]) -> None:
        self._cols: List[str] = list(data.keys())
        lengths = [len(v) for v in data.values()]
        if lengths and len(set(lengths)) != 1:
            raise ValueError(
                f"SimpleDataFrame: all columns must have the same length; got {dict(zip(self._cols, lengths))}"
            )
        n = lengths[0] if lengths else 0
        self._rows: List[List[Any]] = [
            [data[c][i] for c in self._cols]
            for i in range(n)
        ]

    # ---- pandas-compatible properties --------------------------------

    @property
    def columns(self) -> _ColumnsList:
        return _ColumnsList(self._cols)

    @property
    def values(self) -> _RowsView:
        return _RowsView(self._rows)

    @property
    def iloc(self) -> _ILocIndexer:
        return _ILocIndexer(self._rows, len(self._cols))

    def __len__(self) -> int:
        return len(self._rows)

    # ---- column access / subset ----------------------------------------

    def __getitem__(self, key):
        """
        Support two access patterns:
        - ``df['ColName']``          → list of values for that column
        - ``df[['ColA', 'ColB']]``   → new SimpleDataFrame with only those columns
        """
        if isinstance(key, list):
            idx_map = {c: i for i, c in enumerate(self._cols)}
            data = {c: [row[idx_map[c]] for row in self._rows] for c in key}
            return SimpleDataFrame(data)
        # Single column → return as list (compatible with callers that
        # do ``df[col_name]`` expecting a sequence of values)
        idx = self._cols.index(key)
        return [row[idx] for row in self._rows]

    def __setitem__(self, key: str, values) -> None:
        """Allow ``df['NewCol'] = [...]`` to add or replace a column."""
        vals = list(values)
        if key in self._cols:
            col_idx = self._cols.index(key)
            for i, row in enumerate(self._rows):
                row[col_idx] = vals[i] if i < len(vals) else None
        else:
            self._cols.append(key)
            for i, row in enumerate(self._rows):
                row.append(vals[i] if i < len(vals) else None)

    @classmethod
    def from_records(cls, records: List[Dict[str, Any]]) -> 'SimpleDataFrame':
        """Create a SimpleDataFrame from a list-of-dicts (like pd.DataFrame(list))."""
        if not records:
            return cls({})
        keys = list(records[0].keys())
        return cls({k: [r.get(k) for r in records] for k in keys})

    def to_string(self, index: bool = True) -> str:
        """Render the DataFrame as a plain-text aligned table (mirrors pandas)."""
        def _fmt(v) -> str:
            if v is None:
                return 'None'
            if isinstance(v, float):
                return f'{v:.6g}'
            return str(v)

        str_cols = [str(c) for c in self._cols]
        str_rows = [[_fmt(row[c]) for c in range(len(self._cols))] for row in self._rows]
        col_widths = [
            max(len(str_cols[c]), max((len(r[c]) for r in str_rows), default=0))
            for c in range(len(self._cols))
        ]
        if index:
            idx_w = len(str(max(len(self._rows) - 1, 0)))
            lines = ['  '.join([''.rjust(idx_w)] + [str_cols[c].rjust(col_widths[c]) for c in range(len(self._cols))])]
            for i, row in enumerate(str_rows):
                lines.append('  '.join([str(i).rjust(idx_w)] + [row[c].rjust(col_widths[c]) for c in range(len(self._cols))]))
        else:
            lines = ['  '.join(str_cols[c].rjust(col_widths[c]) for c in range(len(self._cols)))]
            for row in str_rows:
                lines.append('  '.join(row[c].rjust(col_widths[c]) for c in range(len(self._cols))))
        return '\n'.join(lines)

    def __repr__(self) -> str:
        lines = ['  '.join(str(c) for c in self._cols)]
        for row in self._rows:
            lines.append('  '.join(str(v) for v in row))
        return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Utility function mirroring pd.isnull / pd.isna
# ---------------------------------------------------------------------------

def isnull(value: Any) -> bool:
    """Return True if *value* is None or float('nan')."""
    if value is None:
        return True
    try:
        # float('nan') != float('nan') is True
        return value != value  # type: ignore[operator]
    except Exception:
        return False
