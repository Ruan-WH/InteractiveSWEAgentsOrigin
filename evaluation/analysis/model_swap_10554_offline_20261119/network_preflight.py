"""Check host runtime access and blocked package/network access on an internal bridge."""
import json
import ipaddress
from pathlib import Path
import time
import docker
import requests

NETWORK = 'ambig-swe-eval-offline-20261119'


def ensure_network(client):
    matches = client.networks.list(names=[NETWORK])
    network = matches[0] if matches else client.networks.create(NETWORK, driver='bridge', internal=True)
    network.reload()
    assert network.attrs['Internal'] is True
    return network


def runtime_network_config(client):
    network = ensure_network(client)
    subnet = ipaddress.ip_network(network.attrs['IPAM']['Config'][0]['Subnet'])
    address = str(subnet.network_address + 10)
    assert all(value.get('IPv4Address', '').split('/')[0] != address
               for value in network.attrs.get('Containers', {}).values()), 'Reserved runtime address is already in use'
    endpoint = client.api.create_endpoint_config(ipv4_address=address)
    kwargs = {'network': NETWORK,
              'networking_config': {NETWORK: endpoint},
              'mem_limit': '6g', 'memswap_limit': '8g'}
    return network, address, kwargs


def main():
    client = docker.from_env()
    network, address, kwargs = runtime_network_config(client)
    container = client.containers.run('docker.io/xingyaoww/sweb.eval.x86_64.django_s_django-10554',
        ['-m', 'http.server', '8765', '--bind', '0.0.0.0'],
        entrypoint='/opt/miniconda3/envs/testbed/bin/python',
        ports={'8765/tcp': ('127.0.0.1', None)}, detach=True, **kwargs)
    try:
        container.reload()
        assert address == container.attrs['NetworkSettings']['Networks'][NETWORK]['IPAddress']
        session = requests.Session()
        session.trust_env = False
        host_ok = False
        for _ in range(30):
            try:
                host_ok = session.get('http://' + address + ':8765', timeout=2).status_code == 200
            except requests.RequestException:
                time.sleep(0.2)
            if host_ok:
                break
        assert host_ok, 'Host must still reach the runtime server'
        code = """import json,socket,subprocess
try:
 c=socket.create_connection(('1.1.1.1',443),3);c.close();blocked=False
except OSError:blocked=True
run=subprocess.run(['/opt/miniconda3/envs/testbed/bin/pip','download','django==3.0','--no-deps','--no-cache-dir','--retries','0','--timeout','3','-d','/tmp/network-probe'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
print(json.dumps(dict(direct_external_tcp_blocked=blocked,pip_download_blocked=run.returncode!=0)))
"""
        check = container.exec_run(['/opt/miniconda3/envs/testbed/bin/python', '-c', code])
        assert check.exit_code == 0
        values = json.loads(check.output.decode())
        assert values['direct_external_tcp_blocked'] and values['pip_download_blocked']
        result = dict(network=NETWORK, internal=network.attrs['Internal'], host_runtime_http_ok=host_ok, **values)
        (Path(__file__).resolve().parent / 'network_preflight.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result))
    finally:
        container.remove(force=True)


if __name__ == '__main__':
    main()
