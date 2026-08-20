from orchestrator.transport.tcp import TCPDeviceClient


class FakeLink:
    """Stands in for protocol.LineLink so TCPDeviceClient can be unit tested
    without a real socket -- only the send/close side is relevant here."""

    def __init__(self):
        self.sent = []
        self.closed = False

    def send_line(self, line: str) -> None:
        self.sent.append(line)

    def close(self) -> None:
        self.closed = True


def make_client(link: FakeLink) -> TCPDeviceClient:
    client = object.__new__(TCPDeviceClient)  # skip __init__'s real socket.create_connection
    client._link = link
    return client


def test_close_sends_quit_and_closes_link():
    link = FakeLink()
    client = make_client(link)

    client.close()

    assert link.sent == ["QUIT\n"]
    assert link.closed


def test_close_still_closes_link_if_quit_send_fails():
    class BrokenSendLink(FakeLink):
        def send_line(self, line: str) -> None:
            raise OSError("connection already gone")

    link = BrokenSendLink()
    client = make_client(link)

    client.close()  # must not raise -- QUIT is best-effort

    assert link.closed
