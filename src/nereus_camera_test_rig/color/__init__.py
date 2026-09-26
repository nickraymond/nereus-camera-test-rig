"""Edge color correction — Phase 8 (SPEC §4, §20).

Pure numpy / OpenCV so it runs on the Mac, the Pi and the backend. Must never import the
capture path (``web``, ``cameras``, ``capture``, ``controller``, serial) or ``rawpy`` —
``tests/unit/test_color_import_boundary.py`` checks this at runtime.
"""
