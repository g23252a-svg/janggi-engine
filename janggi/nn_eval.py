"""Use a trained net as the engine's evaluation function (run wherever torch is
installed — your PC). The live engine calls evaluate(board); this lets you swap
in the neural value head instead of the hand-crafted evaluate().

Wiring it in (in search.py / evaluate usage) is opt-in: only if a net file is
present and torch importable. If torch is missing, the engine keeps using the
classic evaluate() with zero behaviour change — so nothing breaks on machines
without the net.

The net outputs value in [-1, 1] from HAN's perspective. The classic evaluate()
returns centipawn-ish scores where positive = CHO good. To keep the rest of the
search unchanged we convert: nn_eval returns the SAME sign convention as
evaluate() (positive = CHO good) and a comparable scale.
"""
from __future__ import annotations

_NET = None
_DEVICE = "cpu"
_TORCH = None


def load_net(path: str, device: str | None = None) -> bool:
    """Load a trained net for evaluation. Returns False (and stays disabled) if
    torch isn't available, so callers can fall back to the classic eval."""
    global _NET, _DEVICE, _TORCH
    try:
        import torch
        from janggi.nn_model import JanggiNet
    except Exception:
        return False
    _TORCH = torch
    _DEVICE = device or ("cuda" if torch.cuda.is_available() else "cpu")
    net = JanggiNet().to(_DEVICE)
    net.load_state_dict(torch.load(path, map_location=_DEVICE))
    net.eval()
    _NET = net
    return True


def nn_available() -> bool:
    return _NET is not None


# Scale factor to map the net's [-1,1] value onto the classic centipawn scale
# (~ a few thousand at decisive). Tuned so NN and classic scores are roughly
# interchangeable inside the search's alpha/beta bounds.
_VALUE_SCALE = 2000.0


def nn_evaluate(board) -> int:
    """Evaluate `board` with the net. Sign matches the classic evaluate():
    positive = CHO good, negative = HAN good."""
    from janggi.nn_model import board_to_tensor
    from janggi.board import HAN
    with _TORCH.no_grad():
        _policy, value = _NET(board_to_tensor(board, _DEVICE))
        v = float(value.item())  # + = HAN good (net convention)
    # classic evaluate(): + = CHO good, so negate.
    return int(-v * _VALUE_SCALE)
