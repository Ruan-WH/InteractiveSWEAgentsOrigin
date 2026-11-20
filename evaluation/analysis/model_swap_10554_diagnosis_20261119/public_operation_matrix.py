"""Synthetic public-API capability control; never a normal partial-arm input.

Extends the already executed two-field Dimension probe using ordinary ordering
and projection compositions. Contains no benchmark test source or reference fix.
"""

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
rows=[(10,3),(11,1),(16,4),(17,2)]
for pk,rank in rows:Dimension.objects.create(pk=pk,order=rank)
def combined():
 return Dimension.objects.filter(pk__in=[10,11]).union(Dimension.objects.filter(pk__in=[16,17]))
out=[]
for selected in ['pk','order']:
 for ordering in ['pk','-pk','order','-order']:
  for project_first in [False,True]:
   for flat in [False,True]:
    key=0 if ordering.lstrip('-')=='pk' else 1
    idx=0 if selected=='pk' else 1
    expected=[r[idx] for r in sorted(rows,key=lambda r:r[key],reverse=ordering.startswith('-'))]
    if not flat:expected=[(v,) for v in expected]
    original=combined()
    row={'selected':selected,'ordering':ordering,'project_first':project_first,'flat':flat,'expected':expected}
    try:
     if project_first:query=original.values_list(selected,flat=flat).order_by(ordering)
     else:query=original.order_by(ordering).values_list(selected,flat=flat)
     actual=list(query)
     row.update(actual=actual,passed=actual==expected)
    except Exception as error:
     row.update(error=type(error).__name__+': '+str(error),passed=False)
    out.append(row)
print(json.dumps({'origin':'Synthetic public two-field Dimension API composition control, no hidden tests','cases':out,'passed':sum(r['passed'] for r in out),'total':len(out)}))
'''
