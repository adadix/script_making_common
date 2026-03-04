"""
Basic tests for utils.data_export — covers previously-uncovered functions
including Excel export, plot generation, and filename helpers.
All tests use tmp_path (no writes to Logs/).
"""
import sys
import os
import pytest
import pandas as pd
import numpy as np

_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.data_export import (
    export_dataframe_to_excel,
    export_multiple_sheets,
    plot_vf_curve,
    plot_before_after,
    create_timestamped_filename,
    ensure_logs_directory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vf_df(n=6):
    """Return a minimal VF-curve DataFrame: [WP, Voltage(V), Freq(MHz)]."""
    wps    = list(range(n))
    volts  = [1.0 - i * 0.05 for i in range(n)]
    freqs  = [3600 - i * 200 for i in range(n)]
    return pd.DataFrame({'WP': wps, 'Voltage (V)': volts, 'Freq (MHz)': freqs})


# ---------------------------------------------------------------------------
# export_dataframe_to_excel
# ---------------------------------------------------------------------------

class TestExportDataframeToExcel:
    def test_creates_file(self, tmp_path):
        df   = _make_vf_df()
        path = str(tmp_path / 'out.xlsx')
        result = export_dataframe_to_excel(df, path)
        assert result is True
        assert os.path.exists(path)

    def test_file_readable(self, tmp_path):
        df   = _make_vf_df()
        path = str(tmp_path / 'out.xlsx')
        export_dataframe_to_excel(df, path)
        df2 = pd.read_excel(path, sheet_name='VF Curve')
        assert list(df2.columns) == list(df.columns)
        assert len(df2) == len(df)

    def test_custom_sheet_name(self, tmp_path):
        df   = _make_vf_df()
        path = str(tmp_path / 'custom.xlsx')
        result = export_dataframe_to_excel(df, path, sheet_name='MySheet')
        assert result is True
        df2 = pd.read_excel(path, sheet_name='MySheet')
        assert len(df2) == len(df)

    def test_returns_false_on_invalid_path(self):
        df = _make_vf_df()
        result = export_dataframe_to_excel(df, '/invalid_path/nope/out.xlsx')
        assert result is False

    def test_empty_dataframe(self, tmp_path):
        df   = pd.DataFrame({'A': [], 'B': []})
        path = str(tmp_path / 'empty.xlsx')
        result = export_dataframe_to_excel(df, path)
        assert result is True


# ---------------------------------------------------------------------------
# export_multiple_sheets
# ---------------------------------------------------------------------------

class TestExportMultipleSheets:
    def test_creates_file_with_two_sheets(self, tmp_path):
        path   = str(tmp_path / 'multi.xlsx')
        result = export_multiple_sheets({'S1': _make_vf_df(), 'S2': _make_vf_df(3)}, path)
        assert result is True
        assert os.path.exists(path)

    def test_sheet_names_correct(self, tmp_path):
        path = str(tmp_path / 'multi.xlsx')
        export_multiple_sheets({'Alpha': _make_vf_df(), 'Beta': _make_vf_df()}, path)
        xl = pd.ExcelFile(path)
        assert set(xl.sheet_names) == {'Alpha', 'Beta'}

    def test_returns_false_on_invalid_path(self):
        result = export_multiple_sheets({'S': _make_vf_df()}, '/no/such/dir/x.xlsx')
        assert result is False


# ---------------------------------------------------------------------------
# plot_vf_curve
# ---------------------------------------------------------------------------

class TestPlotVfCurve:
    def test_creates_png(self, tmp_path):
        df   = _make_vf_df()
        path = str(tmp_path / 'curve.png')
        result = plot_vf_curve(df, 'TestDomain', path)
        assert result is True
        assert os.path.getsize(path) > 0

    def test_without_interpolation(self, tmp_path):
        df   = _make_vf_df()
        path = str(tmp_path / 'curve_noint.png')
        result = plot_vf_curve(df, 'TestDomain', path, interp_enabled=False)
        assert result is True

    def test_two_point_df(self, tmp_path):
        """DataFrame with only 2 rows — forces linear interpolation path."""
        df   = _make_vf_df(2)
        path = str(tmp_path / 'two.png')
        result = plot_vf_curve(df, 'TwoPt', path)
        assert result is True

    def test_all_nan_returns_false(self, tmp_path):
        df   = pd.DataFrame({'WP': [0, 1], 'V': [float('nan'), float('nan')],
                              'F': [float('nan'), float('nan')]})
        path = str(tmp_path / 'nan.png')
        result = plot_vf_curve(df, 'NaN', path)
        assert result is False


# ---------------------------------------------------------------------------
# plot_before_after
# ---------------------------------------------------------------------------

class TestPlotBeforeAfter:
    def test_creates_png(self, tmp_path):
        df   = _make_vf_df()
        path = str(tmp_path / 'ba.png')
        result = plot_before_after(df, df, 'TestDomain', path)
        assert result is True
        assert os.path.getsize(path) > 0

    def test_empty_after_df(self, tmp_path):
        df_b = _make_vf_df()
        df_a = pd.DataFrame({'WP': [], 'V': [], 'F': []})
        path = str(tmp_path / 'ba_empty.png')
        result = plot_before_after(df_b, df_a, 'Domain', path)
        assert result is True


# ---------------------------------------------------------------------------
# create_timestamped_filename
# ---------------------------------------------------------------------------

class TestCreateTimestampedFilename:
    def test_returns_string(self):
        name = create_timestamped_filename('vf_test', 'xlsx')
        assert isinstance(name, str)

    def test_contains_prefix(self):
        name = create_timestamped_filename('my_prefix', 'xlsx')
        assert 'my_prefix' in name

    def test_contains_extension(self):
        name = create_timestamped_filename('pfx', 'png')
        assert name.endswith('.png')

    def test_different_calls_differ(self):
        import time
        n1 = create_timestamped_filename('x', 'xlsx')
        time.sleep(1.1)
        n2 = create_timestamped_filename('x', 'xlsx')
        assert n1 != n2


# ---------------------------------------------------------------------------
# ensure_logs_directory
# ---------------------------------------------------------------------------

class TestEnsureLogsDirectory:
    def test_returns_string(self):
        result = ensure_logs_directory()
        assert isinstance(result, str)

    def test_directory_exists(self):
        result = ensure_logs_directory()
        assert os.path.isdir(result)
