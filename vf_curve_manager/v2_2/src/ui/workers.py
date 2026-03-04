"""Worker threads for VF Curve Manager UI operations.

BumpWorkerThread and CustomizeWorkerThread run long-running hardware
operations in a background QThread so the UI remains responsive.

Note: flatten operations use an inline FlattenWorker class defined
inside CurveManagerUI._show_progress_dialog_for_flatten().
"""
import sys
import os
from PyQt5.QtCore import QThread, pyqtSignal

# Ensure src/ is on path when this module is imported standalone
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

class BumpWorkerThread(QThread):
    """Worker thread for bump operations to avoid blocking UI during reset."""
    finished = pyqtSignal(dict)  # Emit results when done
    error = pyqtSignal(str)  # Emit error message
    
    def __init__(self, curve_engine, domains, bump_mv, direction):
        super().__init__()
        self.curve_engine = curve_engine
        self.domains = domains
        self.bump_mv = bump_mv
        self.direction = direction
    
    def run(self):
        """Run bump operation in background thread."""
        try:
            results = self.curve_engine.bump_voltages(self.domains, self.bump_mv, self.direction)
            if 'error' in results:
                self.error.emit(results['error'])
            else:
                self.finished.emit(results)
        except Exception as ex:
            self.error.emit(f"Bump operation failed: {ex}")



class CustomizeWorkerThread(QThread):
    """Worker thread for customize frequency operations to avoid blocking UI during reset."""
    finished = pyqtSignal(dict)  # Emit result_data when done
    error = pyqtSignal(str)  # Emit error message
    
    def __init__(self, curve_engine, domain_name, custom_frequencies):
        super().__init__()
        self.curve_engine = curve_engine
        self.domain_name = domain_name
        self.custom_frequencies = custom_frequencies
    
    def run(self):
        """Run customize operation in background thread."""
        try:
            result = self.curve_engine.customize_frequency(self.domain_name, self.custom_frequencies)
            self.finished.emit(result)
        except Exception as ex:
            self.error.emit(f"Customize operation failed: {ex}")


