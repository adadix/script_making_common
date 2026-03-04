"""Tests for utils.process_utils.terminate_openipc()."""
import sys, os, pytest
from unittest.mock import patch, MagicMock

_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.process_utils import terminate_openipc


class TestTerminateOpenipc:
    def test_returns_false_when_no_processes_found(self):
        mock_psutil = MagicMock()
        mock_psutil.process_iter.return_value = []
        with patch.dict('sys.modules', {'psutil': mock_psutil}):
            result = terminate_openipc()
        assert result is False

    def test_kills_openipc_process_via_psutil(self):
        mock_proc = MagicMock()
        mock_proc.info = {'pid': 1234, 'name': 'openipc.exe'}
        mock_psutil = MagicMock()
        mock_psutil.process_iter.return_value = [mock_proc]
        mock_psutil.NoSuchProcess = ProcessLookupError
        mock_psutil.AccessDenied = PermissionError
        with patch.dict('sys.modules', {'psutil': mock_psutil}):
            result = terminate_openipc()
        mock_proc.kill.assert_called_once()
        assert result is True

    def test_ipccli_process_is_killed(self):
        mock_proc = MagicMock()
        mock_proc.info = {'pid': 5678, 'name': 'ipccli_server'}
        mock_psutil = MagicMock()
        mock_psutil.process_iter.return_value = [mock_proc]
        mock_psutil.NoSuchProcess = ProcessLookupError
        mock_psutil.AccessDenied = PermissionError
        with patch.dict('sys.modules', {'psutil': mock_psutil}):
            result = terminate_openipc()
        mock_proc.kill.assert_called_once()

    def test_unrelated_process_is_not_killed(self):
        mock_proc = MagicMock()
        mock_proc.info = {'pid': 42, 'name': 'explorer.exe'}
        mock_psutil = MagicMock()
        mock_psutil.process_iter.return_value = [mock_proc]
        mock_psutil.NoSuchProcess = ProcessLookupError
        mock_psutil.AccessDenied = PermissionError
        with patch.dict('sys.modules', {'psutil': mock_psutil}):
            result = terminate_openipc()
        mock_proc.kill.assert_not_called()
        assert result is False

    def test_access_denied_is_silently_ignored(self):
        mock_proc = MagicMock()
        mock_proc.info = {'pid': 99, 'name': 'openipc.exe'}
        mock_proc.kill.side_effect = PermissionError('access denied')
        mock_psutil = MagicMock()
        mock_psutil.process_iter.return_value = [mock_proc]
        mock_psutil.NoSuchProcess = ProcessLookupError
        mock_psutil.AccessDenied = PermissionError
        with patch.dict('sys.modules', {'psutil': mock_psutil}):
            # Should not raise
            terminate_openipc()

    def test_falls_back_to_powershell_when_psutil_missing(self):
        mock_run = MagicMock()
        mock_run.return_value = MagicMock(returncode=0, stdout='')
        with patch.dict('sys.modules', {'psutil': None}):
            with patch('subprocess.run', mock_run):
                result = terminate_openipc()
        assert result is False   # no PIDs found in empty stdout

    def test_returns_false_on_subprocess_exception(self):
        with patch.dict('sys.modules', {'psutil': None}):
            with patch('subprocess.run', side_effect=Exception('cmd not found')):
                result = terminate_openipc()
        assert result is False
