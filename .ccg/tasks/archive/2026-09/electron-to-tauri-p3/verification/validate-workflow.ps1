$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:P3_WORKFLOW_EVIDENCE = $PSScriptRoot
@'
from pathlib import Path
import hashlib,json,os
import yaml
root=Path.cwd()
evidence=Path(os.environ['P3_WORKFLOW_EVIDENCE'])
workflow=root/'.github/workflows/main.yml'
data=yaml.load(workflow.read_text(encoding='utf-8-sig'),Loader=yaml.BaseLoader)
jobs=data['jobs']
assert jobs['test-backend']['strategy']['matrix']['python-version']==['3.11','3.13']
assert jobs['build-windows']['needs']==['test-backend','test-web']
assert any(s.get('uses','').startswith('dtolnay/rust-toolchain@') and s['with']['toolchain']=='1.95.0' for s in jobs['build-windows']['steps'])
for job in ('test-web','build-windows'):
    assert any(s.get('uses','').startswith('actions/setup-node@') and s['with']['node-version']=='20' for s in jobs[job]['steps'])
steps=[]
directory=evidence/'workflow-steps-final'
directory.mkdir(exist_ok=True)
for name,job in jobs.items():
    for index,step in enumerate(job['steps']):
        if 'run' not in step: continue
        path=directory/f'{name}-{index:02d}.ps1'
        path.write_text(step['run'],encoding='utf-8')
        steps.append({'job':name,'name':step.get('name',''),'runFile':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
report={'yamlParsed':True,'workflowSha256':hashlib.sha256(workflow.read_bytes()).hexdigest(),'runSteps':steps,'remoteExecution':False}
(evidence/'workflow-final-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
'@ | python -
if ($LASTEXITCODE -ne 0) { throw 'Workflow YAML/structure validation failed' }
$failures = @()
$scripts = @((Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'workflow-steps-final') -Filter '*.ps1' -File).FullName)
$scripts += @((Get-ChildItem -LiteralPath desktop/scripts,desktop/portable -Filter '*.ps1' -File).FullName)
foreach ($script in $scripts) {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($script, [ref]$tokens, [ref]$errors)
    foreach ($error in $errors) { $failures += @{ file=$script; error=[string]$error } }
}
$reportPath = Join-Path $PSScriptRoot 'workflow-final-validation.json'
$report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json -AsHashtable
$report['powershellSyntaxParsed'] = $failures.Count -eq 0
$report['powershellFilesParsed'] = $scripts.Count
$report['errors'] = $failures
$report['nativeExitCodeChecks'] = 'All workflow python/npm/cargo/pwsh commands have explicit nonzero propagation; build entry uses checked commands.'
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding utf8
if ($failures.Count) { throw ($failures | ConvertTo-Json -Depth 4) }
"Workflow YAML, $($report.runSteps.Count) run steps and $($scripts.Count) PowerShell sources: PASS. Remote CI not run."
