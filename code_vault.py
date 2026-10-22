"""Store the downloaded class gallery on disk in a scrambled form.

The gallery cache holds classmates' strategy code so the app can run matches against them
offline. Students asked that this code not be readable just by opening the cache file. So the
JSON is compressed and XOR-ed with a keystream made from a random key kept next to the cache
(one key per install), then base64-encoded.

This is obfuscation, not encryption against a determined user: the key sits on the same machine,
because the app itself must read the code back to run it. It stops casual viewing (opening the
file, searching it, copying it to a friend without the key file).

Older caches written as plain JSON are still read, and are rewritten scrambled on first read.
"""
import base64
import hashlib
import json
import os
import zlib

MAGIC = b'IPD-VAULT-1:'
KEY_FILE = '.gallery_key'


def _key_path(path):
    return os.path.join(os.path.dirname(os.path.abspath(path)), KEY_FILE)


def _load_key(path):
    kp = _key_path(path)
    try:
        with open(kp, 'rb') as f:
            key = f.read()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = os.urandom(32)
    tmp = kp + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(key)
    os.replace(tmp, kp)
    return key


def _keystream(seed, n):
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(seed + counter.to_bytes(8, 'big')).digest()
        counter += 1
    return bytes(out[:n])


def _xor(data, stream):
    if not data:
        return b''
    return (int.from_bytes(data, 'big') ^ int.from_bytes(stream, 'big')).to_bytes(len(data), 'big')


def dumps(obj, path):
    """Scramble ``obj`` (JSON-able) with the key that belongs to ``path``'s folder."""
    raw = zlib.compress(json.dumps(obj).encode('utf-8'))
    nonce = os.urandom(16)
    body = _xor(raw, _keystream(_load_key(path) + nonce, len(raw)))
    return MAGIC + base64.b64encode(nonce + body)


def loads(blob, path):
    """Inverse of ``dumps``. Raises ValueError if the data is damaged or the key is wrong."""
    try:
        packed = base64.b64decode(blob[len(MAGIC):], validate=True)
        nonce, body = packed[:16], packed[16:]
        raw = _xor(body, _keystream(_load_key(path) + nonce, len(body)))
        return json.loads(zlib.decompress(raw).decode('utf-8'))
    except (ValueError, zlib.error, UnicodeDecodeError) as e:
        raise ValueError(f'gallery cache unreadable: {e}') from None


def save_list(path, items):
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(dumps(items, path))
    os.replace(tmp, path)


def load_list(path):
    """Read a list saved by ``save_list`` (or an old plain-JSON cache). Missing or damaged
    files read as an empty list, so the app falls back to "press Refresh"."""
    try:
        with open(path, 'rb') as f:
            blob = f.read()
    except OSError:
        return []
    try:
        if blob.startswith(MAGIC):
            data = loads(blob, path)
        else:
            data = json.loads(blob.decode('utf-8'))   # old plain cache
            if isinstance(data, list):
                try:
                    save_list(path, data)                # rewrite it scrambled
                except OSError:
                    pass
    except ValueError:
        return []
    return data if isinstance(data, list) else []
