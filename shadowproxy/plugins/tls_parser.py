import os
import hmac
import random
import struct
import iofree
import hashlib
import binascii
from time import time


def pack_uint16(s):
    return len(s).to_bytes(2, "big") + s


def sni(host):
    return b"\x00\x00" + pack_uint16(pack_uint16(pack_uint16(b"\x00" + host)))


@iofree.parser
def tls1_2_request(plugin):
    tls_version = plugin.tls_version
    tls_plaintext_head = memoryview((yield from iofree.read(5)))
    assert (
        tls_plaintext_head[:3] == b"\x16\x03\x01"
    ), "invalid tls head: handshake(22) protocol_version(3.1)"
    length = int.from_bytes(tls_plaintext_head[-2:], "big")
    assert length == length & 0x3FFF, f"{length} is over 2^14"
    fragment = memoryview((yield from iofree.read(length)))
    assert fragment[0] == 1, "expect client_hello: msg_type(1)"
    handshake_length = int.from_bytes(fragment[1:4], "big")
    client_hello = fragment[4 : handshake_length + 4]
    assert client_hello[:2] == tls_version, "expect: client_version(3.3)"
    verify_id = client_hello[2:34]
    # TODO: replay attact detect
    gmt_unix_time = int.from_bytes(verify_id[:4], "big")
    time_diff = (int(time()) & 0xFFFFFFFF) - gmt_unix_time
    assert abs(time_diff) < plugin.time_tolerance, f"expired request: {time_diff}"
    session_length = client_hello[34]
    assert session_length >= 32, "session length should be >= 32"
    session_id = client_hello[35 : 35 + session_length].tobytes()
    sha1 = hmac.new(
        plugin.proxy.cipher.master_key + session_id, verify_id[:22], hashlib.sha1
    ).digest()[:10]
    assert verify_id[22:] == sha1, "hmac verify failed"
    tail = client_hello[35 + session_length :]
    cipher_suites = tail[:2].tobytes()
    compression_methods = tail[2:3]
    (cipher_suites, compression_methods)
    utc_time = int(time()) & 0xFFFFFF
    random_bytes = utc_time.to_bytes(4, "big") + os.urandom(18)
    random_bytes += hmac.new(
        plugin.proxy.cipher.master_key + session_id, random_bytes, hashlib.sha1
    ).digest()[:10]
    server_hello = (
        tls_version
        + random_bytes
        + session_length.to_bytes(1, "big")
        + session_id
        + binascii.unhexlify(b"c02f000005ff01000100")
    )
    server_hello = b"\x02\x00" + pack_uint16(server_hello)
    server_hello = b"\x16" + tls_version + pack_uint16(server_hello)
    if random.randint(0, 8) < 1:
        ticket = os.urandom((struct.unpack(">H", os.urandom(2))[0] % 164) * 2 + 64)
        ticket = struct.pack(">H", len(ticket) + 4) + b"\x04\x00" + pack_uint16(ticket)
        server_hello += b"\x16" + tls_version + ticket
    server_hello += b"\x14" + tls_version + b"\x00\x01\x01"
    finish_len = random.choice([32, 40])
    server_hello += (
        b"\x16"
        + tls_version
        + struct.pack(">H", finish_len)
        + os.urandom(finish_len - 10)
    )
    server_hello += hmac.new(
        plugin.proxy.cipher.master_key + session_id, server_hello, hashlib.sha1
    ).digest()[:10]
    yield from iofree.write(server_hello)
    yield from ChangeCipherReader(plugin, session_id)
    return "done"


def ChangeCipherReader(plugin, session_id):
    data = memoryview((yield from iofree.read(11)))
    assert data[0] == 0x14, f"{data[0]} != change_cipher_spec(20) {data.tobytes()}"
    assert (
        data[1:3] == plugin.tls_version
    ), f"{data[1:3].tobytes()} != version({plugin.tls_version})"
    assert data[3:6] == b"\x00\x01\x01", "bad ChangeCipherSpec"
    assert data[6] == 0x16, f"{data[6]} != Finish(22)"
    assert (
        data[7:9] == plugin.tls_version
    ), f"{data[7:9]} != version({plugin.tls_version})"
    assert data[9] == 0x00, f"{data[9]} != Finish(0)"
    verify_len = int.from_bytes(data[9:11], "big")
    verify = memoryview((yield from iofree.read(verify_len)))
    sha1 = hmac.new(
        plugin.proxy.cipher.master_key + session_id,
        b"".join([data, verify[:-10]]),
        hashlib.sha1,
    ).digest()[:10]
    assert sha1 == verify[-10:], "hmac verify failed"


@iofree.parser
def application_data(plugin):
    while True:
        data = memoryview((yield from iofree.read(5)))
        assert data[0] == 0x17, f"{data[0]} != application_data(23) {data.tobytes()}"
        assert (
            data[1:3] == plugin.tls_version
        ), f"{data[1:3].tobytes()} != version({plugin.tls_version})"
        size = int.from_bytes(data[3:], "big")
        assert size == size & 0x3FFF, f"{size} is over 2^14"
        data = yield from iofree.read(size)
        yield from iofree.write(data)
