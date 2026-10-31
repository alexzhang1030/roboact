"""TCP proxy for A6: forward, hard cut (FIN/RST), half-open (drop bytes, no close)."""
from __future__ import annotations

import socket
import threading
import time


class TcpProxy:
    def __init__(self, dest_host: str, dest_port: int) -> None:
        self.dest = (dest_host, dest_port)
        self._mode = "forward"  # forward | drop | dead
        self._lock = threading.Lock()
        self._pairs: list[tuple[socket.socket, socket.socket]] = []
        self._held: list[socket.socket] = []
        self._alive = True
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(64)
        self._srv.settimeout(0.25)
        self.host, self.port = self._srv.getsockname()
        self._accept_t = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_t.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def half_open(self) -> None:
        """Stop forwarding. Keep sockets. No FIN/RST to the server."""
        with self._lock:
            self._mode = "drop"

    def cut(self) -> None:
        """Close every socket so the browser sees a real disconnect."""
        with self._lock:
            self._mode = "dead"
            pairs = list(self._pairs)
            held = list(self._held)
            self._pairs.clear()
            self._held.clear()
        for a, b in pairs:
            self._shutdown(a)
            self._shutdown(b)
        for s in held:
            self._shutdown(s)

    def restore(self) -> None:
        with self._lock:
            self._mode = "forward"
            held = list(self._held)
            self._held.clear()
        for s in held:
            self._shutdown(s)

    def close(self) -> None:
        self._alive = False
        self.cut()
        try:
            self._srv.close()
        except OSError:
            pass

    def _shutdown(self, s: socket.socket) -> None:
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            s.close()
        except OSError:
            pass

    def _accept_loop(self) -> None:
        while self._alive:
            try:
                client, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                mode = self._mode
            if mode == "drop":
                client.settimeout(0.5)
                with self._lock:
                    self._held.append(client)
                threading.Thread(target=self._hold, args=(client,), daemon=True).start()
                continue
            if mode == "dead":
                self._shutdown(client)
                continue
            try:
                dest = socket.create_connection(self.dest, timeout=2)
            except OSError:
                self._shutdown(client)
                continue
            client.settimeout(0.2)
            dest.settimeout(0.2)
            with self._lock:
                self._pairs.append((client, dest))
            threading.Thread(target=self._pipe, args=(client, dest), daemon=True).start()
            threading.Thread(target=self._pipe, args=(dest, client), daemon=True).start()

    def _hold(self, client: socket.socket) -> None:
        try:
            while self._alive:
                with self._lock:
                    if self._mode != "drop" or client not in self._held:
                        break
                try:
                    data = client.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
        except OSError:
            pass

    def _pipe(self, src: socket.socket, dst: socket.socket) -> None:
        try:
            while self._alive:
                try:
                    data = src.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    with self._lock:
                        mode = self._mode
                    if mode == "drop":
                        time.sleep(0.2)
                        continue
                    break
                with self._lock:
                    mode = self._mode
                if mode == "forward":
                    try:
                        dst.sendall(data)
                    except OSError:
                        break
                elif mode == "drop":
                    continue
                else:
                    break
        except OSError:
            pass
