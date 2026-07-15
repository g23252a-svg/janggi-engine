"""PUCT Monte-Carlo tree search for neural self-play.

The classic engine remains the fast production default.  This module is the
training/search bridge required by an AlphaZero-style loop: a predictor supplies
legal-move priors plus a value in HAN's fixed perspective, and MCTS turns those
estimates into a stronger visit distribution.

The module deliberately has no torch dependency.  ``janggi.nn_eval`` provides
the PyTorch predictor, while tests and experiments can inject any callable.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable

from .board import Board, Move, HAN

MoveKey = tuple[int, int, int, int]
PolicyValueFn = Callable[[Board], tuple[dict[MoveKey, float], float]]


@dataclass
class _Node:
    prior: float
    to_play: int
    visit_count: int = 0
    value_sum: float = 0.0  # always stored from HAN's perspective
    children: dict[Move, "_Node"] = field(default_factory=dict)
    expanded: bool = False
    terminal_value: float | None = None

    @property
    def mean_value(self) -> float:
        if self.visit_count == 0:
            return 0.0
        return self.value_sum / self.visit_count


class NeuralMCTS:
    """PUCT search using fixed-HAN-perspective policy/value predictions."""

    def __init__(
        self,
        predictor: PolicyValueFn,
        simulations: int = 200,
        c_puct: float = 1.5,
        dirichlet_alpha: float = 0.3,
        dirichlet_fraction: float = 0.25,
        seed: int | None = None,
    ) -> None:
        if simulations < 1:
            raise ValueError("simulations must be positive")
        self.predictor = predictor
        self.simulations = simulations
        self.c_puct = c_puct
        self.dirichlet_alpha = dirichlet_alpha
        self.dirichlet_fraction = dirichlet_fraction
        self._rng = random.Random(seed)

    def search(
        self,
        board: Board,
        temperature: float = 0.0,
        add_root_noise: bool = False,
        forbidden_moves: set[MoveKey] | None = None,
    ) -> tuple[Move | None, dict[MoveKey, float]]:
        """Return a selected move and the normalized root visit distribution.

        ``board`` is mutated during simulations but is guaranteed to be restored,
        including when the predictor raises.
        """
        root = _Node(prior=1.0, to_play=board.side_to_move)
        self._expand(root, board)
        if forbidden_moves:
            root.children = {
                move: child for move, child in root.children.items()
                if move.as_tuple() not in forbidden_moves
            }
            prior_total = sum(child.prior for child in root.children.values())
            if prior_total > 0.0:
                for child in root.children.values():
                    child.prior /= prior_total
        if not root.children:
            return None, {}
        if add_root_noise:
            self._add_root_noise(root)

        for _ in range(self.simulations):
            node = root
            path = [root]
            made = 0
            try:
                while node.expanded and node.children:
                    move, node = self._select_child(node)
                    board.make(move)
                    made += 1
                    path.append(node)

                if node.terminal_value is not None:
                    value = node.terminal_value
                else:
                    value = self._expand(node, board)
                for visited in path:
                    visited.visit_count += 1
                    visited.value_sum += value
            finally:
                for _ in range(made):
                    board.unmake()

        visits = {move.as_tuple(): child.visit_count for move, child in root.children.items()}
        total = sum(visits.values())
        if total <= 0:
            policy = {key: 1.0 / len(visits) for key in visits}
        else:
            policy = {key: count / total for key, count in visits.items()}

        move = self._sample_move(root, temperature)
        return move, policy

    def _expand(self, node: _Node, board: Board) -> float:
        legal = board.legal_moves(node.to_play)
        node.expanded = True
        if not legal:
            # In this engine, mate and stalemate both lose for the side to move.
            node.terminal_value = 1.0 if -node.to_play == HAN else -1.0
            return node.terminal_value

        priors, value = self.predictor(board)
        raw = [max(0.0, float(priors.get(move.as_tuple(), 0.0))) for move in legal]
        total = sum(raw)
        if total <= 0.0:
            raw = [1.0] * len(legal)
            total = float(len(legal))
        node.children = {
            move: _Node(prior=prior / total, to_play=-node.to_play)
            for move, prior in zip(legal, raw)
        }
        return max(-1.0, min(1.0, float(value)))

    def _select_child(self, node: _Node) -> tuple[Move, _Node]:
        sqrt_parent = math.sqrt(max(1, node.visit_count))

        def score(item: tuple[Move, _Node]) -> float:
            _move, child = item
            # Values are stored from HAN's perspective. CHO therefore minimizes
            # the value, expressed here as maximizing side * Q.
            exploitation = node.to_play * child.mean_value
            exploration = (
                self.c_puct * child.prior * sqrt_parent / (1 + child.visit_count)
            )
            return exploitation + exploration

        return max(node.children.items(), key=score)

    def _add_root_noise(self, root: _Node) -> None:
        children = list(root.children.values())
        if not children or self.dirichlet_fraction <= 0.0:
            return
        noise = [self._rng.gammavariate(self.dirichlet_alpha, 1.0) for _ in children]
        total = sum(noise)
        if total <= 0.0:
            return
        frac = self.dirichlet_fraction
        for child, sample in zip(children, noise):
            child.prior = (1.0 - frac) * child.prior + frac * (sample / total)

    def _sample_move(self, root: _Node, temperature: float) -> Move:
        items = list(root.children.items())
        if temperature <= 1e-8:
            return max(items, key=lambda item: item[1].visit_count)[0]

        exponent = 1.0 / temperature
        weights = [float(child.visit_count) ** exponent for _, child in items]
        if sum(weights) <= 0.0:
            weights = [child.prior for _, child in items]
        threshold = self._rng.random() * sum(weights)
        running = 0.0
        for (move, _child), weight in zip(items, weights):
            running += weight
            if running >= threshold:
                return move
        return items[-1][0]
