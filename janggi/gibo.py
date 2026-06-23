"""Game records (기보): save, load, and replay.

A record is stored as JSON so it can be replayed exactly and, later, mined to
build an opening book or tune evaluation weights. The format:

    {
      "version": 1,
      "cho_formation": "msm_s",
      "han_formation": "msm_s",
      "moves": [
        {"fr": 6, "fc": 2, "tr": 6, "tc": 3, "captured": null, "side": "cho"},
        ...
      ],
      "result": "han" | "cho" | "draw" | null,
      "note": "optional free text"
    }

A human-readable text rendering is also available via `to_text`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .board import Board, Move, HAN, CHO, FORMATIONS

GIBO_VERSION = 1

PIECE_KO = {"K": "궁", "C": "차", "P": "포", "M": "마", "S": "상", "G": "사", "J": "졸"}


@dataclass
class Gibo:
    cho_formation: str = "msm_s"
    han_formation: str = "msm_s"
    moves: list[dict] = field(default_factory=list)
    result: str | None = None
    note: str = ""

    # --------------------------------------------------------------- record
    def add_move(self, mv: Move, side: int) -> None:
        self.moves.append(
            {
                "fr": mv.fr, "fc": mv.fc, "tr": mv.tr, "tc": mv.tc,
                "captured": mv.captured,
                "side": "cho" if side == CHO else "han",
            }
        )

    # ------------------------------------------------------------- (de)serialize
    def to_json(self) -> str:
        return json.dumps(
            {
                "version": GIBO_VERSION,
                "cho_formation": self.cho_formation,
                "han_formation": self.han_formation,
                "moves": self.moves,
                "result": self.result,
                "note": self.note,
            },
            ensure_ascii=False,
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> "Gibo":
        data = json.loads(text)
        if data.get("cho_formation") not in FORMATIONS or data.get("han_formation") not in FORMATIONS:
            raise ValueError("invalid or missing formation in gibo")
        g = cls(
            cho_formation=data["cho_formation"],
            han_formation=data["han_formation"],
            moves=data.get("moves", []),
            result=data.get("result"),
            note=data.get("note", ""),
        )
        return g

    # ----------------------------------------------------------------- replay
    def starting_board(self) -> Board:
        return Board.standard(self.cho_formation, self.han_formation)

    def replay(self) -> list[Board]:
        """Return the board state after each move (for stepping through)."""
        board = self.starting_board()
        states = []
        for m in self.moves:
            mv = Move(m["fr"], m["fc"], m["tr"], m["tc"], m.get("captured"))
            board.make(mv)
            states.append(_snapshot(board))
        return states

    def validate(self) -> tuple[bool, str]:
        """Check that every recorded move was legal from its position."""
        board = self.starting_board()
        for i, m in enumerate(self.moves):
            side = CHO if m["side"] == "cho" else HAN
            legal = board.legal_moves(side)
            target = (m["fr"], m["fc"], m["tr"], m["tc"])
            if not any(lm.as_tuple() == target for lm in legal):
                return False, f"move {i+1} ({m['side']}) is illegal: {target}"
            board.make(Move(m["fr"], m["fc"], m["tr"], m["tc"], m.get("captured")))
        return True, "ok"

    # ------------------------------------------------------------------ text
    def to_text(self) -> str:
        lines = [
            f"# Janggi gibo",
            f"# 초 {self.cho_formation} / 한 {self.han_formation}",
        ]
        for i, m in enumerate(self.moves):
            side = "초" if m["side"] == "cho" else "한"
            cap = f" x{PIECE_KO.get(m['captured'], '')}" if m.get("captured") else ""
            lines.append(
                f"{i+1:>3}. {side} ({m['fr']},{m['fc']})->({m['tr']},{m['tc']}){cap}"
            )
        if self.result:
            lines.append(f"# result: {self.result}")
        return "\n".join(lines)


def _snapshot(board: Board) -> Board:
    b = Board()
    b.grid = [row[:] for row in board.grid]
    b.side_to_move = board.side_to_move
    return b
