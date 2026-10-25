"""The local app only answers requests addressed to localhost (review, Oct 2026: a foreign Host
header could list, save and delete local gallery entries, the DNS-rebinding route), and treats
the IPv4-mapped loopback address as loopback."""
import pytest

import app as ipd

pytestmark = pytest.mark.security

EVIL = 'evil.example.com'


@pytest.mark.parametrize('host', ['localhost', 'localhost:5000', '127.0.0.1:5000', '[::1]:5000'])
def test_loopback_hosts_are_served(client, host):
    assert client.get('/local-gallery/list', headers={'Host': host}).status_code == 200


@pytest.mark.parametrize('host', [EVIL, EVIL + ':5000', '127.0.0.1.evil.example.com'])
def test_foreign_host_is_refused(client, host):
    assert client.get('/local-gallery/list', headers={'Host': host}).status_code == 403
    r = client.post('/local-gallery/delete', json={'name': 'x'}, headers={'Host': host})
    assert r.status_code == 403


def test_foreign_origin_cannot_change_data(client):
    r = client.post('/local-gallery/delete', json={'name': 'x'},
                    headers={'Origin': 'https://' + EVIL})
    assert r.status_code == 403
    r = client.post('/local-gallery/delete', json={'name': 'x'},
                    headers={'Origin': 'http://127.0.0.1:5000'})
    assert r.status_code != 403


@pytest.mark.parametrize('addr, ok', [('127.0.0.1', True), ('::1', True),
                                      ('::ffff:127.0.0.1', True), ('192.168.1.5', False),
                                      ('localhost', False), ('', False), (None, False)])
def test_loopback_addresses(addr, ok):
    assert ipd._is_loopback_addr(addr) is ok


def test_lan_client_is_refused(client):
    r = client.get('/local-gallery/list', environ_base={'REMOTE_ADDR': '192.168.1.5'})
    assert r.status_code == 403
