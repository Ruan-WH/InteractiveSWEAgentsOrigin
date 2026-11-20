"""Compare public API compositions on baseline, model proposal, and gold in a private control container."""
import io
import json
from pathlib import Path
import tarfile

import docker

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
BASE = '14d026cccb144c6877294ba4cd4e03ebf0842498'
PROBE = '''import json
import django
from django.conf import settings
settings.configure(DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':':memory:'}},INSTALLED_APPS=[])
django.setup()
from django.db import models,connection
class Dimension(models.Model):
 order=models.IntegerField()
 class Meta:app_label='public_probe'
with connection.schema_editor() as editor:editor.create_model(Dimension)
for pk,rank in [(10,3),(11,1),(16,4),(17,2)]:Dimension.objects.create(pk=pk,order=rank)
qs=Dimension.objects.filter(pk__in=[10,11]).union(Dimension.objects.filter(pk__in=[16,17])).order_by('order')
first=repr(qs)
cleared=list(qs.order_by().values_list('pk',flat=True))
last=repr(qs)
result={'reported_sequence_passes':first==last,'ordered_original_pks':[obj.pk for obj in qs],'cleared_projection_pks':cleared}
try:
 result['ordered_projection_pks']=list(qs.values_list('pk',flat=True))
 result['ordered_projection_preserves_result']=result['ordered_projection_pks']==[11,17,10,16]
except Exception as error:
 result['ordered_projection_error']=type(error).__name__+': '+str(error)
print(json.dumps(result))
'''


def main():
    client = docker.from_env()
    container = client.containers.run('docker.io/xingyaoww/sweb.eval.x86_64.django_s_django-10554',
                  entrypoint='/bin/sleep', command=['600'], network_mode='none', detach=True)
    def execute(args):
        out=container.exec_run(args)
        assert out.exit_code == 0, out.output.decode()[-2000:]
        return out.output.decode()
    def probe():
        return json.loads(execute(['/opt/miniconda3/envs/testbed/bin/python','-c',PROBE]))
    try:
        result={'protocol':'researcher-control-only-not-agent-input','baseline':probe()}
        focused=json.loads((HERE/'focused_response.json').read_text())['response']
        for edit in focused['edits']:
            assert edit['path'] in ('django/db/models/sql/compiler.py','django/db/models/sql/query.py','django/db/models/query.py')
        code="import json;edits=json.loads("+repr(json.dumps(focused['edits']))+");\nfor e in edits:\n p='/testbed/'+e['path'];s=open(p).read();assert s.count(e['old_str'])==1;open(p,'w').write(s.replace(e['old_str'],e['new_str']))"
        execute(['/opt/miniconda3/envs/testbed/bin/python','-c',code])
        result['focused_model']=probe()
        patch=execute(['git','-C','/testbed','diff',BASE])
        (HERE/'focused_model.patch').write_text(patch)
        execute(['git','-C','/testbed','reset','--hard',BASE])
        dataset=json.loads((PROJECT/'evaluation/evaluation_outputs/qwen-new7-grounded-v1-20261101/official_eval/dataset.verified.json').read_text())
        row,=[r for r in dataset if r['instance_id']=='django__django-10554']
        data=row['patch'].encode();buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w')as archive:
            info=tarfile.TarInfo('gold.patch');info.size=len(data);archive.addfile(info,io.BytesIO(data))
        container.put_archive('/tmp',buffer.getvalue())
        execute(['git','-C','/testbed','apply','/tmp/gold.patch'])
        result['gold']=probe()
        (HERE/'public_composition_controls.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
    finally:container.remove(force=True)


if __name__=='__main__':
    main()
