"""Audit running diagnostic containers without modifying their source or feedback."""
import json
from pathlib import Path
import docker

HERE = Path(__file__).resolve().parent


def main():
    client = docker.from_env()
    for name, network_name in [('evidence-readable', 'ambig-swe-diagnosis-readable-20261119'),
                               ('evidence-sampled', 'ambig-swe-diagnosis-sampled-20261119')]:
        network = client.networks.get(network_name)
        network.reload()
        records = []
        for identifier in network.attrs.get('Containers', {}):
            container = client.containers.get(identifier)
            container.reload()
            code = "import json,socket; blocked=False\ntry:\n c=socket.create_connection(('1.1.1.1',443),2);c.close()\nexcept OSError:blocked=True\nprint(json.dumps({'external_tcp_blocked':blocked}))"
            probe = container.exec_run(['/opt/miniconda3/envs/testbed/bin/python', '-c', code])
            assert probe.exit_code == 0
            values = json.loads(probe.output.decode())
            assert network.attrs['Internal'] and values['external_tcp_blocked']
            records.append({'id': container.id, 'networks': list(container.attrs['NetworkSettings']['Networks']),
                            'internal': network.attrs['Internal'], **values})
        assert records, name + ': no live diagnostic runtime to audit'
        (HERE / (name + '.network.json')).write_text(json.dumps(records, indent=2) + '\n')
        print(name, 'isolated runtime audited')


if __name__ == '__main__':
    main()
