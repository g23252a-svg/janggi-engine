"""Use a trained net as the engine's evaluation function (run wherever torch is
installed — your PC). The live engine calls evaluate(board); this lets you swap
in the neural value head instead of the hand-crafted evaluate().

Wiring it in (in search.py / evaluate usage) is opt-in: only if a net file is
present and torch importable. If torch is missing, the engine keeps using the
classic evaluate() with zero behaviour change — so nothing breaks on machines
without the net.

The net outputs value in [-1, 1] from HAN's perspective. The classic evaluate()
uses the same sign convention (positive = HAN good), so the value only needs to
be scaled before it is passed to ``search.Engine(evaluator=nn_evaluate)``.
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


def value_to_score(value: float) -> int:
    """Convert a HAN-perspective network value to the engine score scale."""
    return int(value * _VALUE_SCALE)


def nn_evaluate(board) -> int:
    """Evaluate `board` with the net. Sign matches the classic evaluate():
    positive = HAN good, negative = CHO good."""
    if _NET is None or _TORCH is None:
        raise RuntimeError("neural net is not loaded; call load_net(path) first")
    from janggi.nn_model import board_to_tensor
    with _TORCH.no_grad():
        _policy, value = _NET(board_to_tensor(board, _DEVICE))
        v = float(value.item())  # + = HAN good (net convention)
    return value_to_score(v)


def nn_policy_value(board) -> tuple[dict[tuple[int, int, int, int], float], float]:
    """Return legal-move priors and a HAN-perspective value for NeuralMCTS."""
    if _NET is None or _TORCH is None:
        raise RuntimeError("neural net is not loaded; call load_net(path) first")
    from janggi.nn_model import board_to_tensor, move_to_index

    legal = board.legal_moves(board.side_to_move)
    with _TORCH.no_grad():
        logits, value = _NET(board_to_tensor(board, _DEVICE))
        if not legal:
            return {}, float(value.item())
        indices = _TORCH.tensor(
            [move_to_index(*move.as_tuple()) for move in legal],
            dtype=_TORCH.long,
            device=_DEVICE,
        )
        legal_logits = logits[0].index_select(0, indices)
        probabilities = _TORCH.softmax(legal_logits, dim=0).detach().cpu().tolist()
    return (
        {move.as_tuple(): float(prob) for move, prob in zip(legal, probabilities)},
        float(value.item()),
    )


class NeuralPredictor:
    """Independent loaded network, suitable for candidate-vs-champion arenas."""

    def __init__(self, path: str, device: str | None = None) -> None:
        import torch
        from janggi.nn_model import JanggiNet

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.net = JanggiNet().to(self.device)
        self.net.load_state_dict(torch.load(path, map_location=self.device))
        self.net.eval()

    def evaluate(self, board) -> int:
        """Engine-compatible static score (positive = HAN)."""
        from janggi.nn_model import board_to_tensor

        with self.torch.no_grad():
            _logits, value = self.net(board_to_tensor(board, self.device))
        return value_to_score(float(value.item()))

    def __call__(
        self, board
    ) -> tuple[dict[tuple[int, int, int, int], float], float]:
        """MCTS-compatible legal policy priors plus HAN-perspective value."""
        from janggi.nn_model import board_to_tensor, move_to_index

        legal = board.legal_moves(board.side_to_move)
        with self.torch.no_grad():
            logits, value = self.net(board_to_tensor(board, self.device))
            if not legal:
                return {}, float(value.item())
            indices = self.torch.tensor(
                [move_to_index(*move.as_tuple()) for move in legal],
                dtype=self.torch.long,
                device=self.device,
            )
            probabilities = self.torch.softmax(
                logits[0].index_select(0, indices), dim=0
            ).detach().cpu().tolist()
        return (
            {move.as_tuple(): float(prob) for move, prob in zip(legal, probabilities)},
            float(value.item()),
        )
