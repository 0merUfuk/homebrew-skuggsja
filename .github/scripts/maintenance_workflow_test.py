#!/usr/bin/env python3
"""Exercise the maintenance workflow with fake gh; no live PR or branch writes."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
TEXT = (REPO / '.github/workflows/update-skuggsja.yml').read_text()


def extract_maintenance(workflow):
    """Extract only the expected literal Python block; actionlint handles YAML."""
    lines = workflow.splitlines()
    starts = [i for i, line in enumerate(lines) if line == '  maintenance:']
    if len(starts) != 1:
        raise ValueError('Expected exactly one maintenance job')
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines))
                if re.fullmatch(r'  [A-Za-z_][A-Za-z0-9_-]*:', lines[i])), len(lines))
    job = lines[start + 1:end]
    markers = [i for i, line in enumerate(job) if line == '        run: |']
    if len(markers) != 1:
        raise ValueError('Expected one literal maintenance run block')
    body = job[markers[0] + 1:]
    while body and not body[-1].strip():
        body.pop()
    if any(line.strip() and not line.startswith('          ') for line in body):
        raise ValueError('Unexpected indentation or additional maintenance steps')
    body = [line[10:] if line.strip() else '' for line in body]
    if not body or body[0] != "python3 - <<'PY'" or body[-1] != 'PY':
        raise ValueError('Expected the inline Python here-document without extra shell commands')
    return '\n'.join(job), '\n'.join(body[1:-1])


MAINTENANCE, SCRIPT = extract_maintenance(TEXT)
FAKE = r"""import json,os,sys
from pathlib import Path
args=sys.argv[1:]
root=Path(os.environ['FAKE_ROOT']);mode=os.environ['FAKE_MODE']
assert args[:3]==['api','--hostname','github.com']
method=args[args.index('--method')+1];endpoint=args[args.index('--method')+2]
assert endpoint.startswith('repos/0merUfuk/homebrew-skuggsja/')
body=json.load(sys.stdin) if '--input' in args else None
with (root/'calls.jsonl').open('a') as f:f.write(json.dumps({'method':method,'endpoint':endpoint,'payload':body})+'\n')
branch='maintenance/receipt-recovery';head='a'*40
pr={'state':'open','head':{'ref':branch,'sha':head,'repo':{'full_name':'0merUfuk/homebrew-skuggsja'}},'base':{'ref':'main','repo':{'full_name':'0merUfuk/homebrew-skuggsja'}},'html_url':'https://example.invalid/pr/1'}
if '/git/ref/heads/' in endpoint:
 assert method=='GET'
 count=root/'ref-count';n=int(count.read_text())+1 if count.exists() else 1;count.write_text(str(n))
 sha='b'*40 if mode=='advanced' or (mode=='advanced-before-create' and n>=2) or (mode=='advanced-after-create' and n>=3) else head
 print(json.dumps({'ref':'refs/heads/'+branch,'object':{'type':'commit','sha':sha}}))
elif '/pulls?' in endpoint:
 assert method=='GET'
 if mode=='foreign-pr':pr['head']['repo']['full_name']='other/tap'
 if mode=='wrong-pr-head':pr['head']['sha']='b'*40
 print(json.dumps([pr,pr] if mode=='duplicate' else [pr] if mode in ['reuse','foreign-pr','wrong-pr-head'] else []))
elif endpoint.endswith('/pulls'):
 assert method=='POST'
 assert body['head']==branch and body['base']=='main'
 assert 'Approve workflows to run' in body['body'] and 'without an administrator bypass' in body['body']
 print(json.dumps(pr))
else:sys.exit(90)
"""

class Maintenance(unittest.TestCase):
    def run_case(self,mode='new',**overrides):
        with tempfile.TemporaryDirectory(prefix='skuggsja-maintenance-') as temporary:
            root=Path(temporary);(root/'bin').mkdir();gh=root/'bin/gh'
            gh.write_text('#!'+sys.executable+'\n'+FAKE);gh.chmod(0o700)
            env={'PATH':str(root/'bin'),'GH_TOKEN':'synthetic-unusable-token','FAKE_ROOT':str(root),'FAKE_MODE':mode,
                 'GITHUB_REPOSITORY':'0merUfuk/homebrew-skuggsja','GITHUB_EVENT_NAME':'workflow_dispatch',
                 'GITHUB_REF':'refs/heads/maintenance/receipt-recovery','GITHUB_SHA':'a'*40,'REQUESTED_TAG':''}
            env.update(overrides)
            command=[sys.executable,'-c',SCRIPT]
            if sys.platform=='darwin':
                profile=root/'network.sb';profile.write_text('(version 1)\n(allow default)\n(deny network*)\n')
                command=['/usr/bin/sandbox-exec','-f',str(profile),*command]
            result=subprocess.run(command,cwd=root,env=env,text=True,capture_output=True,timeout=15)
            log=root/'calls.jsonl';calls=[json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            self.last={'mode':mode,'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr,'calls':calls}
            return result,calls
    def reject(self,mode='new',**env):
        result,calls=self.run_case(mode,**env);self.assertNotEqual(result.returncode,0,self.last)
        self.assertFalse(any(call['method']!='GET' for call in calls),self.last)
    def test_new_pr_only_writes_one_pull_request(self):
        result,calls=self.run_case();self.assertEqual(result.returncode,0,self.last)
        self.assertEqual([c['method'] for c in calls],['GET','GET','GET','POST','GET'])
        self.assertEqual(json.loads(result.stdout)['status'],'maintenance_pr_open')
    def test_reuse_is_read_only(self):
        result,calls=self.run_case('reuse');self.assertEqual(result.returncode,0,self.last)
        self.assertTrue(all(c['method']=='GET' for c in calls))
        self.assertEqual(json.loads(result.stdout)['status'],'maintenance_pr_reused')
    def test_wrong_repository(self):self.reject(GITHUB_REPOSITORY='other/tap')
    def test_wrong_event(self):self.reject(GITHUB_EVENT_NAME='push')
    def test_main_is_rejected(self):self.reject(GITHUB_REF='refs/heads/main')
    def test_tag_ref_is_rejected(self):self.reject(GITHUB_REF='refs/tags/v0.1.1')
    def test_nonempty_tag_is_rejected(self):self.reject(REQUESTED_TAG='v0.1.1')
    def test_invalid_commit_is_rejected(self):self.reject(GITHUB_SHA='HEAD')
    def test_shell_characters_in_branch_are_rejected(self):self.reject(GITHUB_REF='refs/heads/$(touch injected)')
    def test_advanced_head_is_rejected(self):self.reject('advanced')
    def test_head_advanced_before_create_is_rejected(self):self.reject('advanced-before-create')
    def test_duplicate_open_prs_are_rejected(self):self.reject('duplicate')
    def test_foreign_pr_is_rejected(self):self.reject('foreign-pr')
    def test_wrong_pr_head_is_rejected(self):self.reject('wrong-pr-head')
    def test_head_advanced_after_create_is_never_reported_success(self):
        result,calls=self.run_case('advanced-after-create');self.assertNotEqual(result.returncode,0)
        self.assertEqual(sum(c['method']=='POST' for c in calls),1)
        self.assertEqual(result.stdout,'')
    def test_no_checkout_and_main_importer_stays_main_only(self):
        main_jobs = re.findall(r"(?ms)^  (?:test|propose):\n(.*?)(?=^  [A-Za-z_][A-Za-z0-9_-]*:\n|\Z)", TEXT)
        self.assertEqual(len(main_jobs), 2)
        for job in main_jobs:
            self.assertIn("    if: github.repository == '0merUfuk/homebrew-skuggsja' && github.ref == 'refs/heads/main'", job)
        self.assertNotIn('uses:',MAINTENANCE)
        self.assertNotIn('contents: write',MAINTENANCE)
        self.assertIn('contents: read',MAINTENANCE)
        self.assertIn('pull-requests: write',MAINTENANCE)
        self.assertIn("github.event_name == 'workflow_dispatch'",MAINTENANCE)
        self.assertIn("github.ref != 'refs/heads/main'",MAINTENANCE)

if __name__=='__main__':
    unittest.main(verbosity=2)
