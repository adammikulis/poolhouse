"""Loopback transport with a simulated kernel-supplied remote client."""

from poolhouse.fleet.framing import LimitedServer

REMOTE_ADDRESS = "192.0.2.10"


class RemoteClientServer(LimitedServer):
    """Run real TLS and handlers with a fixed remote client on a loopback socket."""

    def __init__(self, handler, *, tls):
        if tls is None:
            raise ValueError("the simulated remote client requires a TLS context")
        super().__init__(("127.0.0.1", 0), handler, tls=tls)

    def get_request(self):
        sock, address = super().get_request()
        assert address[0] == "127.0.0.1"
        assert sock.getsockname()[0] == "127.0.0.1"
        return sock, (REMOTE_ADDRESS, address[1])
