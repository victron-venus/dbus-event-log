# Reproducing the maintained-source coverage bound

This is the verification recipe used for the dated evidence, not an application or release entry point. Copy the Python block below to a temporary file outside the checkout, for example `/tmp/audit-scope.py`. Install the official [shfmt 3.14.1 release](https://github.com/mvdan/sh/releases/tag/v3.14.1), verifying the published digest for your platform. The recorded measurement used the Darwin ARM64 binary; a different platform has a different binary digest. Use Python with PyYAML available (the project validation environment includes it).

```bash
SHFMT=/absolute/path/to/shfmt .venv-ci/bin/python /tmp/audit-scope.py "$PWD" > /tmp/coverage-scope.json
```

The script reads tracked source and the existing measured Python coverage JSON. It parses source without running project scripts and writes only its report to stdout. The recorded totals are reproducible from the reviewed commit. Embedded command forms, language classification and the exclusions were also reviewed manually; this recipe is tailored to these projects, not a universal static-analysis parser. Reassess it when adding languages or other ways to execute embedded code. The Python and shell statement units differ: no shell execution is credited, compound shell statements are included, and extra Docker/Compose/text-tool allowances make the bound conservative.

```python
from pathlib import Path
import ast,hashlib,json,re,shlex,subprocess,sys,os,shutil
import yaml
SH=Path(os.environ.get('SHFMT') or shutil.which('shfmt') or 'shfmt')
assert subprocess.check_output([str(SH),'-version'],text=True).strip()=='v3.14.1'
def nodes(x):
 if isinstance(x,dict):
  yield x
  for v in x.values():yield from nodes(v)
 elif isinstance(x,list):
  for v in x:yield from nodes(v)
def scan_shell(src,key,records,depth=0):
 if depth>5:raise ValueError('Nested shell too deep')
 clean=re.sub(r'\$\{\{.*?\}\}', 'EXPRESSION',src,flags=re.S)
 p=subprocess.run([str(SH),'-ln','bash','-tojson'],input=clean,text=True,capture_output=True,check=True)
 tree=json.loads(p.stdout); ns=list(nodes(tree)); total=sum('Cmd' in n for n in ns)
 records.append({'path':key,'language':'Shell AST statements (including compound statements)','zero_coverage_statements':total,'sha256':hashlib.sha256(src.encode()).hexdigest()})
 for n in ns:
  if 'Hdoc' in n:
   h=n['Hdoc']; body=''.join(q.get('Value','EXPANSION') for q in h['Parts']); k=f'{key}:heredoc@{h["Pos"]["Line"]}'
   if body.lstrip().startswith(('import ','from ')):
    records.append({'path':k,'language':'embedded Python AST statements','zero_coverage_statements':sum(isinstance(v,ast.stmt) for v in ast.walk(ast.parse(body))),'sha256':hashlib.sha256(body.encode()).hexdigest()})
   else:scan_shell(body,k,records,depth+1)
  if n.get('Type')!='CallExpr':continue
  args=n.get('Args',[])
  def literal(w):
   raw=clean.encode()[w['Pos']['Offset']:w['End']['Offset']].decode()
   val=shlex.split(raw)
   return val[0] if len(val)==1 else raw
  words=[literal(w) for w in args]
  if not words:continue
  key2=f'{key}:command@{n["Pos"]["Line"]}'
  if words[0]=='ssh' and len(words)==3:scan_shell(words[-1],key2+':ssh',records,depth+1)
  elif words[0]=='trap' and len(words)>1:scan_shell(words[1],key2+':trap',records,depth+1)
  elif '-c' in words and any('python' in w for w in words[:words.index('-c')]):
   code=words[words.index('-c')+1]
   records.append({'path':key2+':python-c','language':'embedded Python AST statements','zero_coverage_statements':sum(isinstance(v,ast.stmt) for v in ast.walk(ast.parse(code))),'sha256':hashlib.sha256(code.encode()).hexdigest()})
  elif words[0] in ('awk','sed'):
   # Conservative expression-unit allowance in addition to shell command itself.
   text=' '.join(words[1:]); unit=1+len(re.findall(r';|\{|\}',text))
   records.append({'path':key2+':text-tool','language':'sed/awk conservative expression units','zero_coverage_statements':unit,'sha256':hashlib.sha256(text.encode()).hexdigest()})
for root_arg in sys.argv[1:]:
 root=Path(root_arg).resolve();name=root.name;files=subprocess.check_output(['git','ls-files'],cwd=root,text=True).splitlines();records=[]
 for f in files:
  p=root/f
  if not p.is_file():continue
  try:src=p.read_text()
  except UnicodeError:continue
  if f.endswith('.sh') or re.match(r'#!.*\b(?:bash|sh|dash|ash)\b',src):scan_shell(src,f,records)
  if f.startswith('.github/workflows/') and f.endswith(('.yml','.yaml')):
   wf=yaml.load(src,Loader=yaml.BaseLoader)
   for job,body in wf.get('jobs',{}).items():
    for i,step in enumerate(body.get('steps',[])):
     if 'run' in step:scan_shell(step['run'],f'{f}:jobs.{job}.steps[{i}].run',records)
  if p.name in ('docker-compose.yml','compose.yml','docker-compose.yaml','compose.yaml'):
   compose=yaml.load(src,Loader=yaml.BaseLoader)
   for service,cfg in compose.get('services',{}).items():
    for field in ('command','entrypoint'):
     if field in cfg:
      value=json.dumps(cfg[field],sort_keys=True)
      records.append({'path':f'{f}:services.{service}.{field}','language':'Compose declarative command allowance','zero_coverage_statements':1,'sha256':hashlib.sha256(value.encode()).hexdigest()})
  if p.name.startswith('Dockerfile'):
   logical=re.sub(r'\\\n\s*',' ',src)
   lines=[line for line in logical.splitlines() if line.strip() and not line.lstrip().startswith('#')]
   records.append({'path':f,'language':'Docker declarative instruction allowance (in addition to RUN shell)','zero_coverage_statements':len(lines),'sha256':hashlib.sha256(src.encode()).hexdigest()})
   for i,line in enumerate(lines):
    if line.startswith('RUN '):scan_shell(line[4:],f'{f}:RUN[{i}]',records)
 cov=json.loads((root/'docs/evidence/coverage-2026-10-12.json').read_text());extra=sum(x['zero_coverage_statements'] for x in records);lower=cov['covered_statements']/(cov['statements']+extra)*100
 d={'measured_on':'2026-10-12','parser':{'name':'shfmt','version':'3.14.1','binary_sha256':hashlib.sha256(SH.resolve().read_bytes()).hexdigest(),'source_url':'https://github.com/mvdan/sh/releases/tag/v3.14.1'},'method':'Count every Bash AST Stmt node, including compound statements and nested command substitutions, across all tracked shell files (including extensionless shebang scripts) and every GitHub Actions run step. Replace Actions expressions with a non-executable placeholder before parsing. Recursively count heredoc shell, SSH/trap command strings, and count embedded Python heredoc/-c AST statements separately at zero coverage. Add conservative sed/awk expression units and all Docker instructions plus RUN shell. Each declarative Compose command/entrypoint receives another zero-coverage unit. No non-Python execution credit is used.','python_covered_statements':cov['covered_statements'],'python_statements':cov['statements'],'additional_zero_coverage_units':extra,'aggregate_conservative_lower_bound_percent':round(lower,4),'records':records,'limitations':['Coverage.py executable source statements and shell AST statements use different language-appropriate units; this is an explicitly conservative portfolio scope calculation, not a shell-coverage measurement.','External interpreters, installed libraries, tools and remote reusable workflows/actions are dependencies rather than project-maintained source. Test/fixture source and documentation examples are not production source.','YAML/TOML/JSON configuration is data; executable workflow run steps and Docker RUN bodies are included. Docker instructions receive an extra zero-coverage allowance.','Do not apply this bound to native dependency memory safety or branch coverage.']}
 print(json.dumps(d,indent=2))
```
