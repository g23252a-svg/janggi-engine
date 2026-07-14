"""Candidate-vs-champion gating for neural Janggi networks."""
from __future__ import annotations

import argparse
import random

from .board import Board, CHO, HAN, FORMATIONS
from .mcts import NeuralMCTS
from .nn_eval import NeuralPredictor
from .repetition import RepetitionTracker
from .score import judge


def play_match_game(
    candidate: NeuralMCTS,
    champion: NeuralMCTS,
    candidate_side: int,
    max_moves: int = 200,
    cho_formation: str | None = None,
    han_formation: str | None = None,
) -> str:
    """Play one deterministic arena game and return han/cho/draw."""
    forms = list(FORMATIONS)
    board = Board.standard(
        cho_formation or random.choice(forms),
        han_formation or random.choice(forms),
    )
    tracker = RepetitionTracker()
    tracker.record(board)
    forced_winner: str | None = None

    for _ply in range(max_moves):
        side = board.side_to_move
        legal = board.legal_moves(side)
        forbidden = {
            move.as_tuple() for move in legal
            if tracker.would_repeat_thrice(board, move)
        }
        if not legal or len(forbidden) == len(legal):
            forced_winner = "han" if -side == HAN else "cho"
            break

        player = candidate if side == candidate_side else champion
        move, _policy = player.search(
            board,
            temperature=0.0,
            add_root_noise=False,
            forbidden_moves=forbidden,
        )
        if move is None:
            forced_winner = "han" if -side == HAN else "cho"
            break
        board.make(move)
        tracker.record(board)

        if not board.legal_moves(board.side_to_move):
            forced_winner = "han" if side == HAN else "cho"
            break

    return forced_winner or judge(board)["winner"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--champion", required=True)
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--simulations", type=int, default=200)
    ap.add_argument("--threshold", type=float, default=0.55)
    ap.add_argument("--device", default=None)
    ap.add_argument("--seed", type=int, default=20260714)
    args = ap.parse_args()

    if args.games < 2:
        raise SystemExit("--games must be at least 2")
    random.seed(args.seed)
    candidate = NeuralMCTS(
        NeuralPredictor(args.candidate, args.device),
        simulations=args.simulations,
        seed=args.seed,
    )
    champion = NeuralMCTS(
        NeuralPredictor(args.champion, args.device),
        simulations=args.simulations,
        seed=args.seed + 1,
    )

    wins = draws = losses = 0
    opening: tuple[str, str] | None = None
    forms = list(FORMATIONS)
    for game in range(args.games):
        if game % 2 == 0 or opening is None:
            opening = (random.choice(forms), random.choice(forms))
        candidate_side = CHO if game % 2 == 0 else HAN
        winner = play_match_game(
            candidate, champion, candidate_side,
            cho_formation=opening[0], han_formation=opening[1],
        )
        wanted = "han" if candidate_side == HAN else "cho"
        if winner == "draw":
            draws += 1
        elif winner == wanted:
            wins += 1
        else:
            losses += 1
        print(f"{game + 1}/{args.games}: W/D/L {wins}/{draws}/{losses}")

    score = (wins + 0.5 * draws) / args.games
    decision = "PROMOTE" if score >= args.threshold else "REJECT"
    print(f"candidate score={score:.3f}, threshold={args.threshold:.3f}: {decision}")


if __name__ == "__main__":
    main()
