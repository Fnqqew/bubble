"""Rectángulos en coordenadas de pantalla (píxeles físicos)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def offset(self, dx: int, dy: int) -> Rect:
        return Rect(self.left + dx, self.top + dy, self.width, self.height)

    @classmethod
    def from_points(cls, x1: int, y1: int, x2: int, y2: int) -> Rect:
        return cls(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
