"""Verify cleanup in a disposable real instance image before model calls."""
import json
from pathlib import Path
import docker


def main():
    project = Path(__file__).resolve().parents[3]
    script = project / 'evaluation/utils/reference_history.py'
    base = '14d026cccb144c6877294ba4cd4e03ebf0842498'
    setup = """import json,pathlib,subprocess
path=pathlib.Path('/swe_util/eval_data/instances/swe-bench-instance.json')
path.parent.mkdir(parents=True,exist_ok=True)
path.write_text(json.dumps([dict(repo='django/django',instance_id='django__django-10554',base_commit=BASE,version='3',patch='dummy private repair',test_patch='dummy private test')]))
subprocess.check_call(['mkdir','-p','/workspace'])
subprocess.check_call(['cp','-r','/testbed','/workspace/audit'])
subprocess.check_call(['/opt/miniconda3/envs/testbed/bin/python','/tmp/reference_history.py','--workspace','/workspace/audit','--base',BASE])
checks=[]
for root in ['/testbed','/workspace/audit']:
 result=subprocess.run(['git','-C',root,'cat-file','-e','2cbd3967e0'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 checks.append(dict(root=root,reference_commit_missing=result.returncode!=0))
assert all(c['reference_commit_missing'] for c in checks)
print('BLOCKED_REFERENCE_CHECK='+json.dumps(checks))
""".replace('BASE', repr(base))
    client = docker.from_env()
    output = client.containers.run('docker.io/xingyaoww/sweb.eval.x86_64.django_s_django-10554',
        ['-c', setup], entrypoint='/opt/miniconda3/envs/testbed/bin/python',
        volumes={str(script): {'bind': '/tmp/reference_history.py', 'mode': 'ro'}}, remove=True).decode()
    lines = output.splitlines()
    cleanup = json.loads(next(line.split('=', 1)[1] for line in lines if line.startswith('REFERENCE_HISTORY_CLEANED=')))
    checks = json.loads(next(line.split('=', 1)[1] for line in lines if line.startswith('BLOCKED_REFERENCE_CHECK=')))
    result = {'real_container_cleanup': cleanup, 'reference_object_checks': checks,
              'model_calls': 0, 'image_modified': False}
    (Path(__file__).resolve().parent / 'container_preflight.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
