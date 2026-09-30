"""Conexiones abiertas con Deepgram (HTTPS keep-alive). Cada pedido ahorra ~0,2 a 0,4 s de reconexión (el saludo cifrado
con el servidor tarda eso desde Argentina). Las usan la voz en la nube y las frases del jugador enviadas con el botón.
"""

from __future__ import annotations

import http.client
import queue
import select
import time

from .errors import CloudError, error_for

HOST = "api.deepgram.com"
# Deepgram cierra a los ~5 s una conexión inactiva y, si nunca se usó, deja escrito antes un «408 Request Time-out». Con
# un tiempo de guardado mayor, el pedido siguiente leía ese 408 como respuesta («Deepgram respondió 408 <html>…») y la
# frase se procesaba con la PC local.
IDLE_MAX_S = 4.0


def usable(connection: http.client.HTTPConnection) -> bool:
    """Indica si la conexión sirve para otro pedido: no, si el servidor envió algo por su cuenta (un «408») o la cerró.
    """
    sock = connection.sock
    if sock is None:
        return True  # aún sin conectar: se conecta al pedir
    try:
        if getattr(sock, "pending", lambda: 0)():
            return False
        readable, _, _ = select.select([sock], [], [], 0)
    except (OSError, ValueError):
        return False
    return not readable


class Pool:
    def __init__(self, host: str = HOST, timeout: float = 8.0, factory=http.client.HTTPSConnection) -> None:
        self.host = host
        self.timeout = timeout
        self._factory = factory
        self._idle: queue.LifoQueue = queue.LifoQueue()

    def _new(self) -> http.client.HTTPConnection:
        return self._factory(self.host, timeout=self.timeout)

    def _take(self) -> http.client.HTTPConnection:
        while True:
            try:
                connection, since = self._idle.get_nowait()
            except queue.Empty:
                return self._new()
            if time.monotonic() - since < IDLE_MAX_S and usable(connection):
                return connection
            connection.close()

    def _give(self, connection: http.client.HTTPSConnection) -> None:
        self._idle.put((connection, time.monotonic()))

    def warm(self) -> None:
        """Deja una conexión abierta sin enviar nada (sin costo), para que el primer pedido salga de inmediato."""
        connection = self._take()
        try:
            if connection.sock is None:
                connection.connect()
            self._give(connection)
        except OSError:
            connection.close()

    def request(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> bytes:
        """Realiza el pedido y devuelve la respuesta completa. Si la conexión guardada se cortó o el servidor la
        dio por vencida (408), reintenta una vez con una conexión nueva.
        """
        for attempt in (0, 1):
            connection = self._take() if attempt == 0 else self._new()
            try:
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                data = response.read()
            except (http.client.HTTPException, OSError) as exc:
                connection.close()
                if attempt:
                    raise CloudError(f"Sin respuesta de Deepgram: {exc}") from exc
                continue
            if response.status == 408 and not attempt:
                connection.close()  # conexión vencida: se reintenta con una nueva
                continue
            if response.status >= 400:
                connection.close()
                raise error_for(response.status, data[:200].decode("utf-8", "replace"))
            self._give(connection)
            return data
        raise CloudError("Sin respuesta de Deepgram")

    def stream(self, method: str, path: str, body: bytes, headers: dict[str, str], size: int = 4800):
        """Igual que `request`, pero la respuesta se entrega en fragmentos a medida que Deepgram los envía, para
        comenzar a reproducir la voz sin esperar el final.
        """
        for attempt in (0, 1):
            connection = self._take() if attempt == 0 else self._new()
            try:
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
            except (http.client.HTTPException, OSError) as exc:
                connection.close()
                if attempt:
                    raise CloudError(f"Sin respuesta de Deepgram: {exc}") from exc
                continue
            if response.status == 408 and not attempt:
                response.read()
                connection.close()  # conexión vencida: se reintenta con una nueva
                continue
            if response.status >= 400:
                data = response.read()
                connection.close()
                raise error_for(response.status, data[:200].decode("utf-8", "replace"))
            return self._pieces(connection, response, size)
        raise CloudError("Sin respuesta de Deepgram")

    def _pieces(self, connection, response, size: int):
        finished = False
        try:
            while True:
                data = response.read1(size)
                if not data:
                    break
                yield data
            finished = True
        except (http.client.HTTPException, OSError):
            pass
        finally:
            if finished:
                self._give(connection)
            else:
                connection.close()


_pools: dict[float, Pool] = {}


def pool(timeout: float = 8.0) -> Pool:
    """Devuelve el pool asociado a un tiempo de espera (compartido por todo Bubble)."""
    if timeout not in _pools:
        _pools[timeout] = Pool(timeout=timeout)
    return _pools[timeout]
