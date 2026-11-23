"""Freio de janela deslizante: no máximo N eventos nos últimos S segundos."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable


class Janela:
    def __init__(self, limite: int, segundos: float, relogio: Callable[[], float]):
        self.limite = limite
        self.segundos = segundos
        self._relogio = relogio
        self._eventos: deque[float] = deque()

    def _podar(self) -> None:
        corte = self._relogio() - self.segundos
        while self._eventos and self._eventos[0] <= corte:
            self._eventos.popleft()

    def cheia(self) -> bool:
        """Já houve `limite` eventos na janela: o próximo é barrado."""
        self._podar()
        return len(self._eventos) >= self.limite

    def registrar(self, quando: float | None = None) -> None:
        self._eventos.append(self._relogio() if quando is None else quando)

    def espera(self) -> int:
        """Segundos até a janela abrir de novo (0 se já está aberta)."""
        self._podar()
        if len(self._eventos) < self.limite:
            return 0
        libera_em = self._eventos[len(self._eventos) - self.limite] + self.segundos
        return max(1, int(libera_em - self._relogio()) + 1)
