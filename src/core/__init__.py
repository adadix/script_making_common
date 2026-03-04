"""
Core package for VF Curve Manager Tool.

Provides core functionality for:
- Configuration loading from JSON
- VF curve operations engine
"""

from .config_loader import *
from .curve_engine import *

__all__ = ['config_loader', 'curve_engine']
