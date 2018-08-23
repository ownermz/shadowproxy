import random
from curio import socket
from ... import gvars
from ..base.client import ClientBase
from ...utils import pack_addr
from .parser import socks5_response, socks4_response
from ...utils import set_disposable_recv


class SocksClient(ClientBase):
    name = "socks"

    async def init(self):
        response_parser = socks5_response.parser()
        auth = getattr(self.ns, "auth", None)
        if auth:
            methods = b"\x00\x02"
        else:
            methods = b"\x00"

        handshake = b"\x05" + len(methods).to_bytes(1, "big") + methods
        request = b"\x05\x01\x00" + pack_addr(self.target_addr)
        await self.sock.sendall(handshake + request)
        while True:
            data = await self.sock.recv(gvars.PACKET_SIZE)
            if not data:
                raise Exception("socks5 handshake failed")
            response_parser.send(data)
            if response_parser.has_result:
                break
        redundant = response_parser.readall()
        set_disposable_recv(self.sock, redundant)


class Socks4Client(ClientBase):
    async def init(self):
        response_parser = socks4_response.parser()
        info = await socket.getaddrinfo(
            *self.target_addr, socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP
        )
        addr = random.choice(info)[-1]
        handshake = b"\x04\x01" + pack_ipv4(addr)
        await self.sock.sendall(handshake)
        while True:
            data = await self.sock.recv(gvars.PACKET_SIZE)
            if not data:
                raise Exception("socks4 handshake failed")
            response_parser.send(data)
            if response_parser.has_result:
                break
        redundant = response_parser.readall()
        set_disposable_recv(self.sock, redundant)


def pack_ipv4(addr, userid: bytes = b"\x01\x01") -> bytes:
    host, port = addr
    tail = b""
    try:
        packed = socket.inet_aton(host)
    except OSError:
        packed = b"\x00\x00\x00\x01"
        tail = host.encode() + b"\x00"
    return port.to_bytes(2, "big") + packed + userid + b"\x00" + tail
