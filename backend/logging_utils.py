import logging
import sys

_goonline_logger = logging.getLogger("goonline.debug")
if not _goonline_logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("[GO-ONLINE] %(message)s"))
    _goonline_logger.addHandler(_h)
    _goonline_logger.setLevel(logging.INFO)
    _goonline_logger.propagate = False

diag_logger = logging.getLogger("spinr.diag")
if not diag_logger.handlers:
    _h2 = logging.StreamHandler(sys.stdout)
    _h2.setFormatter(logging.Formatter("%(message)s"))
    diag_logger.addHandler(_h2)
    diag_logger.setLevel(logging.INFO)
    diag_logger.propagate = False
