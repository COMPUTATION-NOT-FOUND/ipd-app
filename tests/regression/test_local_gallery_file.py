"""Saved Local strategies survive simultaneous saves and a damaged file (review, Oct 2026: three
parallel saves lost entries, five left invalid JSON that then read as an empty gallery and was
overwritten by the next save)."""
import json
import threading

import pytest

import app as ipd

CODE = "def s(last_moves, my_history, opponents_histories, meta):\n    return 'C'\n"


@pytest.fixture
def gallery_path(tmp_path, monkeypatch):
    path = tmp_path / 'local_gallery.json'
    monkeypatch.setattr(ipd, 'LOCAL_GALLERY_PATH', str(path))
    return path


def _save(client, name):
    return client.post('/save-strategy', json={'name': name, 'code': CODE})


def test_parallel_saves_keep_every_entry(client, app_instance, gallery_path):
    names = [f'strat{i}' for i in range(12)]
    threads = [threading.Thread(target=_save, args=(app_instance.test_client(), n)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    saved = json.loads(gallery_path.read_text(encoding='utf-8'))
    assert sorted(s['name'] for s in saved) == sorted(names)


def test_damaged_file_falls_back_to_the_backup(client, gallery_path):
    assert _save(client, 'one').status_code == 200
    assert _save(client, 'two').status_code == 200    # 'one' alone is now the backup
    gallery_path.write_text('[{"name": "tw', encoding='utf-8')
    names = [s['name'] for s in client.get('/local-gallery/list').get_json()['strategies']]
    assert names == ['one']


def test_damaged_file_with_no_backup_is_never_overwritten(client, gallery_path):
    gallery_path.write_text('[{"name": "precious", "code": "x"', encoding='utf-8')
    r = client.get('/local-gallery/list').get_json()
    assert r['strategies'] == [] and 'damaged' in r['error']
    assert _save(client, 'new').status_code == 500
    assert client.post('/local-gallery/delete', json={'name': 'x'}).status_code == 500
    assert gallery_path.read_text(encoding='utf-8') == '[{"name": "precious", "code": "x"'


def test_missing_file_is_an_empty_gallery(client, gallery_path):
    assert client.get('/local-gallery/list').get_json() == {'strategies': []}
    assert _save(client, 'first').status_code == 200
    assert not (gallery_path.parent / 'local_gallery.json.tmp').exists()
