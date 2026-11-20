"""Independently inspect each live experiment container's network policy."""
import json
from pathlib import Path
import re
import docker

PROJECT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
RAW = PROJECT / 'evaluation/evaluation_outputs/model-swap-10554-offline-20261119'


def main():
    client = docker.from_env()
    for arm in ('ideal_no_followup', 'partial_followup', 'partial_no_followup'):
        target = HERE / ('runtime_network_' + arm + '.json')
        if target.exists():
            continue
        path = RAW / arm / 'inference.console.log'
        if not path.exists():
            continue
        names = re.findall(r'Container started: (openhands-runtime-[\w-]+)', path.read_text())
        if not names:
            continue
        try:
            container = client.containers.get(names[-1])
        except docker.errors.NotFound:
            continue
        networks = container.attrs['NetworkSettings']['Networks']
        assert len(networks) == 1
        settings = {name: client.networks.get(name).attrs['Internal'] for name in networks}
        assert all(settings.values())
        code = """import socket,json
try:
 s=socket.create_connection(('1.1.1.1',443),3);s.close();blocked=False
except OSError:blocked=True
print(json.dumps({'external_tcp_blocked':blocked}))
"""
        check = container.exec_run(['/opt/miniconda3/envs/testbed/bin/python', '-c', code])
        assert check.exit_code == 0
        result = json.loads(check.output.decode())
        assert result['external_tcp_blocked']
        result.update(arm=arm, container=container.name, networks_internal=settings)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result))


if __name__ == '__main__':
    main()
