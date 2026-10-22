"""The RBMK control room: the generated GLB, the instrument list against a run, and the rules."""
import gzip
import hashlib
import json
import struct
import subprocess
from pathlib import Path

import pytest
from rbmk_run import feed_file, model_run, plain_run

pytest_plugins = ['real_model']

APP = Path(__file__).resolve().parents[1] / 'examples' / 'rbmk'
ROOM = APP / 'room'
SPEC_FILE = ROOM / 'instruments.json'
SPEC = json.loads(SPEC_FILE.read_text())


@pytest.fixture(scope='session')
def plain(tmp_path_factory):
    """A finished run of the plain-code naive controller on pump_trip, with the feed file the node check reads."""
    tmp = tmp_path_factory.mktemp('rbmk-plain')
    doc = plain_run(tmp, 'pump_trip', 'naive')
    return doc, feed_file(doc, tmp / 'feed.json')


def glb():
    data = gzip.decompress((ROOM / 'room.glb.gz').read_bytes())
    magic, version, length = struct.unpack_from('<4sII', data, 0)
    assert (magic, version, length) == (b'glTF', 2, len(data))
    size, kind = struct.unpack_from('<I4s', data, 12)
    assert kind == b'JSON'
    return json.loads(data[20:20 + size])


def source_hash():
    h = hashlib.sha256()
    for p in [*sorted((APP / 'generator').glob('*.py')), SPEC_FILE]:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def node_names():
    return {n['name'] for n in glb()['nodes'] if 'name' in n}


def instruments(*types):
    return [i for i in SPEC['instruments'] if i['type'] in types]


def test_glb_is_current_and_labelled_interpretive():
    scene = glb()['scenes'][0]['extras']
    assert scene['build_hash'] == source_hash(), 'room.glb is older than the generator; rebuild it (README)'
    assert 'Interpretive' in scene['interpretive']
    assert (ROOM / 'room.glb.gz').stat().st_size < 1_000_000


def test_every_instrument_has_its_nodes():
    names = node_names()
    need = {'dial': ('face', 'needle'), 'ann': ('face',), 'lamp': ('face',), 'recorder': ('face',),
            'screen': ('face',), 'button': ('cap', 'cover', 'label'), 'rodmap': ()}
    missing = [f'INS_{i["id"]}' + (f'__{part}' if part else '')
               for i in SPEC['instruments'] for part in ('', *need[i['type']]) if f'INS_{i["id"]}' + (f'__{part}' if part else '') not in names]
    assert not missing


def test_no_instrument_node_is_missing_from_the_spec():
    ids = {i['id'] for i in SPEC['instruments']}
    root = {n[4:] for n in node_names() if n.startswith('INS_') and '__' not in n}
    assert root == ids


def test_rod_cells_match_the_demos_211_rods(plain):
    from examples.rbmk.plant.rods import Rods

    rods = [r.id for r in Rods().rods]
    assert len(rods) == 211
    assert {n[4:] for n in node_names() if n.startswith('ROD_')} == set(rods)
    assert len(plain[0]['trace'][0]['rods']) == 211


def test_bookmarks_have_camera_nodes():
    names = node_names()
    for key in SPEC['bookmarks']:
        assert f'CAM_{key}' in names and f'CAMTARGET_{key}' in names
    assert sum(n.startswith('LIGHT_') for n in names) >= 4


def test_dial_recorder_and_rule_fields_exist_in_a_run(plain):
    known = set(SPEC['feed_trace_fields']) | set(SPEC['derived_fields'])
    FEED = plain[0]
    row = FEED['trace'][0]
    assert not [f for f in SPEC['feed_trace_fields'] if f not in row and f not in ('model_risk', 'tip')]
    used = {i['field'] for i in instruments('dial')}
    used |= {s[0] for i in instruments('recorder') for s in i['series']}
    leaves = [r for i in instruments('lamp', 'ann') for r in rule_leaves(i['rule'])]
    used |= {r['field'] for r in leaves if r['when'] == 'trace' or r['when'] == 'index'}
    used |= {r[k] for r in leaves if r['when'] in ('diff', 'diffrow') for k in ('a', 'b')}
    assert used <= known, used - known
    decision_fields = set(next(r for r in FEED['rows'] if r['kind'] == 'decision'))
    assert {r['field'] for r in leaves if r['when'] == 'decision'} <= decision_fields
    assert not [r for i in instruments('lamp', 'ann') for r in rule_leaves(i['rule']) if r['when'] == 'interlock'], 'the plant has no interlock layer'
    prot = {r['id'] for i in instruments('lamp') for r in rule_leaves(i['rule']) if r['when'] == 'prot'}
    assert prot <= set(row['prot'])


def rule_leaves(rule):
    return rule['of'] if rule['when'] == 'all' else [rule]


@pytest.fixture
def model(tmp_path, serve_leases, real):
    """A finished run of the real decision model on the seven-tick scenario; fails naming the cause when the model cannot run."""
    return model_run(tmp_path, real)[0]


@pytest.mark.real_model
def test_a_model_run_names_its_model_and_every_answer_comes_from_it(model):
    meta = model['meta']
    assert meta['controller'] == 'model' and meta['backend']['model'] and 'CANNED' not in meta['backend']['model'].upper()
    answered = [r for r in model['rows'] if r['kind'] == 'decision' and r.get('answers')]
    assert answered and all(a['model'] == meta['backend']['model'] for d in answered for a in d['answers'].values())
    assert all(d['choice']['cls'] in model['classes'] for d in answered)


def run_check(feed_file):
    out = subprocess.run(['node', str(ROOM / 'check.mjs'), str(SPEC_FILE), str(feed_file)],
                         capture_output=True, text=True, check=True, timeout=60).stdout
    return json.loads(out)


def test_rules_agree_with_a_runs_own_values(plain):
    FEED = plain[0]
    got = run_check(plain[1])
    rows = {r['tick']: r for r in got['rows']}
    for row in FEED['trace']:
        lit, dials = rows[row['tick']]['lit'], rows[row['tick']]['dials']
        assert lit['L_SCRAM'] == row['scram']
        assert lit['A_ORM_LOW'] == (row['orm'] < 15)
        assert all(lit[f'L_PUMP_{n + 1}'] == (row['pumps'][n] > 0.05) for n in range(8))
        assert dials['D_FUELT'] == row['Tf'] and dials['D_PRESS'] == row['p']
        assert abs(dials['D_POWER_A'] - row['chA']) < 1e-9
        assert abs(dials['D_ROD_BANK'] - sum(row['rods']) / 211) < 1e-9
    assert any(any(r['lit'].values()) for r in got['rows'])


def test_rod_order_matches_the_simulator(plain):
    from examples.rbmk.plant.rods import Rods

    assert run_check(plain[1])['rodOrder'] == [r.id for r in Rods().rods]
