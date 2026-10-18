"""Tuned evaluator weights: table 1 of the compiled evaluator (evalw=1).

``TUNED_SCALARS`` lists the 45 weights of janggi/_core.pyx's EW table in its
order (see the DEF EW_* indices there); ``TUNED_PST`` is the 720-entry
piece-square table (piece code * 90 + square seen from the owner's side, row 0
being the owner's back rank). ``None`` leaves table 1 equal to the hand-set
table 0.
"""

TUNED_SCALARS = None
TUNED_PST = None
