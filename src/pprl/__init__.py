"""
Privacy-Preserving Record Linkage (PPRL).

The ``pprl`` command line is the only interface. There is no public Python
API, but the tests import the modules they test.
"""

# Import this first, so that pressing Ctrl+C while the other modules load
# stops pprl quietly instead of showing a traceback.
from . import _starting  # noqa: F401

# Then stop with a plain message on a computer that the linkage library has no
# build for, before anything imports it.
from ._platform import stop_if_unsupported

stop_if_unsupported()
