import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_coverage_policy_binds_canonical_topology_v2():
    policy = json.loads((ROOT / 'contracts/coverage-policy.json').read_text(encoding='utf-8'))
    assert policy['topology_contract']['repo'] == 'Aftergraph/after-graph-governance'
    assert policy['topology_contract']['path'] == 'docs/platform-topology/2.0.json'


def test_source_registry_binds_canonical_topology_v2():
    text = (ROOT / 'contracts/sources.yaml').read_text(encoding='utf-8')
    assert 'path: docs/platform-topology/2.0.json' in text
    assert 'path: docs/platform-topology/1.0.json' not in text


def test_coverage_policy_maps_current_topology_v2_roles():
    policy = json.loads((ROOT / 'contracts/coverage-policy.json').read_text(encoding='utf-8'))
    mapped = {role for cfg in policy['concerns'].values() for role in cfg['roles']}
    for role in {
        'human-operator-plane',
        'legacy-migration-source',
        'skill-compatibility-contract',
        'skill-portability-benchmark',
    }:
        assert role in mapped
    assert 'venture-os-consumer' not in mapped
