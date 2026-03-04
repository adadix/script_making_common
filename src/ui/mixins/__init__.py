"""ui.mixins — behaviour mixins for CurveManagerUI.

Each mixin covers one cohesive concern:

  ThemeMixin      — header / footer / button-style constants / theme toggle
  DomainMixin     — sidebar domain buttons, selection state
  OperationsMixin — show-curve, bump, WP-edit, flatten, customize handlers
  DiscoveryMixin  — live register scan, discovered-registers tab
  ProgressMixin   — progress-dialog helpers, worker launchers
"""
from .theme_mixin      import ThemeMixin        # noqa: F401
from .domain_mixin     import DomainMixin        # noqa: F401
from .operations_mixin import OperationsMixin    # noqa: F401
from .discovery_mixin  import DiscoveryMixin     # noqa: F401
from .progress_mixin   import ProgressMixin      # noqa: F401
