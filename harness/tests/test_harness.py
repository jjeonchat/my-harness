import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from core import ROOT, Invalid, approval_valid, digest, evaluate, read
from harness import approve, begin, finish, init_run, repair, resume, run_path, state, write
from fixtures import INPUT, node, seed, snapshot
from guard import check
from figma_collect import normalize

class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'harness'; self.root.mkdir()
        shutil.copy(ROOT/'rules.json',self.root/'rules.json')
        for name in ('prd.md','design.md','story-service.md','story-workflow.md'):
            (self.root.parent/name).write_text('test context')
        init_run('test-run',INPUT,self.root); self.run=self.root/'runs/test-run'; seed(self.run)
    def edit(self,rel,change):
        p=self.run/rel; data=read(p); change(data); write(p,data)
    def approve_fixture(self):
        for stage in (1,2,3):
            begin(self.run,stage,self.root); self.assertTrue(finish(self.run,self.root)['passed'])
        approve(self.run,'test-run 컨셉 v1 승인',self.root)
    def report(self,through=3): return evaluate(self.run,self.root,through)
    def fails(self,rule,through=3):
        result=self.report(through)
        self.assertFalse(result['passed'])
        self.assertTrue(any(c['id']==rule and c['status'] in ('fail','blocked') for c in result['checks']),result)
    def test_normal_lifecycle(self):
        self.approve_fixture()
        for stage in (4,5):
            begin(self.run,stage,self.root); self.assertTrue(finish(self.run,self.root)['passed'])
        self.assertEqual(state(self.run)['stages']['5']['status'],'complete')
    def test_private_default(self):
        self.edit('02-spec/screens.json',lambda x:x['service'].update(default_visibility='member_only')); self.fails('PRIVATE_DEFAULT')
    def test_implicit_visibility(self):
        self.edit('02-spec/screens.json',lambda x:x['service']['transitions'][0].update(user_selected=False)); self.fails('EXPLICIT_VISIBILITY')
    def test_non_seller(self):
        self.edit('02-spec/screens.json',lambda x:x['service'].update(sale_roles=['paid','seller'])); self.fails('SELLER_ONLY')
    def test_unreviewed_approval(self):
        self.edit('02-spec/screens.json',lambda x:x['service']['transitions'][2].update(reviewed_by_operator=False)); self.fails('REVIEW_REQUIRED')
    def test_missing_approval_transition(self):
        self.edit('02-spec/screens.json',lambda x:x['service']['transitions'].pop()); self.fails('REVIEW_REQUIRED')
    def test_phase2_status(self):
        self.edit('02-spec/screens.json',lambda x:x['service']['states'].append('for_sale')); self.fails('MVP_STATES')
    def test_phase2_transition_not_declared_in_states(self):
        self.edit('02-spec/screens.json',lambda x:x['service']['transitions'][0].update(to='suspended')); self.fails('MVP_STATES')
    def test_frame_size(self):
        self.edit('03-concept/key-screens.json',lambda x:x['frames'][0].update(width=400)); self.fails('FRAME_SIZE')
    def test_key_count(self):
        def change(x):
            x['frames'].pop(); x['manifest']['frames'].pop(); x['source']['manifest_hash']=digest(x['manifest'])
        self.edit('03-concept/key-screens.json',change); self.fails('KEY_COUNT')
    def test_shadow(self):
        self.edit('03-concept/key-screens.json',lambda x:x['frames'][0]['nodes'][1]['effects'].append({'type':'DROP_SHADOW','visible':True})); self.fails('NO_SHADOW')
    def test_radius(self):
        self.edit('03-concept/key-screens.json',lambda x:x['frames'][0]['nodes'][1].update(corners=[16]*4)); self.fails('RADIUS')
    def test_text_spacing(self):
        self.edit('03-concept/key-screens.json',lambda x:x['frames'][0]['nodes'][2]['text_runs'][0].update(letter_spacing=1)); self.fails('LETTER_SPACING')
    def test_uppercase(self):
        self.edit('03-concept/key-screens.json',lambda x:x['frames'][0]['nodes'][2]['text_runs'][0].update(text_case='UPPER')); self.fails('TEXT_CASE')
    def test_featured_fill(self):
        self.edit('03-concept/key-screens.json',lambda x:x['frames'][0]['nodes'][1].update(role='featured-pricing',fills=[{'type':'SOLID','color':'#141414','opacity':1}])); self.fails('FEATURED_FILL')
    def test_icon_radius(self):
        self.edit('03-concept/key-screens.json',lambda x:x['frames'][0]['nodes'][1].update(role='app-icon',width=100,height=100,corners=[29]*4)); self.fails('RADIUS')
    def test_missing_evidence(self):
        (self.run/'03-concept/key-screens.json').unlink(); self.fails('VALIDATION_ERRORS')
    def test_empty_subtree(self):
        self.edit('03-concept/key-screens.json',lambda x:x['frames'][0].update(nodes=[])); self.fails('VALIDATION_ERRORS')
    def test_missing_node_styles(self):
        self.edit('03-concept/key-screens.json',lambda x:x['frames'][0]['nodes'][0].pop('effects')); self.fails('VALIDATION_ERRORS')
    def test_unknown_role(self):
        self.edit('03-concept/key-screens.json',lambda x:x['frames'][0]['nodes'][1].update(role='not-classified')); self.fails('VALIDATION_ERRORS')
    def test_malformed_json(self):
        (self.run/'input.json').write_text('{bad'); self.fails('VALIDATION_ERRORS')
    def test_string_boolean(self):
        self.edit('02-spec/screens.json',lambda x:x['service']['transitions'][0].update(user_selected='false')); self.fails('VALIDATION_ERRORS')
    def test_live_rejects_fixture(self):
        self.edit('input.json',lambda x:x.update(mode='live')); self.fails('VALIDATION_ERRORS')
    def test_forged_live_source_without_receipt(self):
        self.edit('input.json',lambda x:x.update(mode='live'))
        self.edit('03-concept/key-screens.json',lambda x:x['source'].update(kind='figma-rest')); self.fails('VALIDATION_ERRORS')
    def test_no_approval(self): self.fails('APPROVAL',4)
    def test_no_implicit_approval(self):
        self.approve_fixture()
        with self.assertRaises(Invalid): approve(self.run,'계속해',self.root)
    def test_stale_approval(self):
        self.approve_fixture(); self.edit('03-concept/key-screens.json',lambda x:x.update(concept_version='v2'))
        self.assertFalse(approval_valid(self.run,self.root))
        with self.assertRaises(Invalid): begin(self.run,4,self.root)
    def test_resume_invalidates_downstream(self):
        self.approve_fixture(); self.edit('02-spec/screens.json',lambda x:x['screens'][0].update(title='changed'))
        result=resume(self.run,self.root); self.assertEqual(result['changed_from'],2)
        self.assertEqual(state(self.run)['stages']['1']['status'],'complete')
        self.assertEqual(state(self.run)['stages']['2']['status'],'pending')
        self.assertFalse(approval_valid(self.run,self.root))
    def test_rules_change_restarts_stage1(self):
        self.approve_fixture(); r=read(self.root/'rules.json'); r['limits']['width']=400; write(self.root/'rules.json',r)
        self.assertEqual(resume(self.run,self.root)['changed_from'],1)
    def test_retry_limit_and_resume(self):
        begin(self.run,1,self.root); finish(self.run,self.root)
        self.edit('02-spec/screens.json',lambda x:x['service'].update(default_visibility='member_only'))
        begin(self.run,2,self.root); self.assertFalse(finish(self.run,self.root)['passed'])
        for count in (1,2):
            result=repair(self.run,'PRIVATE_DEFAULT',self.root); self.assertEqual(result['repair_attempt'],count)
            begin(self.run,2,self.root); self.assertFalse(finish(self.run,self.root)['passed'])
        self.assertTrue(resume(self.run,self.root)['halted'])
        self.assertEqual(state(self.run)['repairs']['PRIVATE_DEFAULT'],2)
        with self.assertRaises(Invalid): begin(self.run,2,self.root)
    def test_retry_requires_registration(self):
        self.edit('01-research/references.json',lambda x:x[0].update(accessible=False))
        begin(self.run,1,self.root); finish(self.run,self.root)
        with self.assertRaises(Invalid): begin(self.run,1,self.root)
    def test_concurrency(self):
        begin(self.run,1,self.root)
        with self.assertRaises(Invalid): begin(self.run,1,self.root)
        init_run('second',INPUT,self.root)
        with self.assertRaises(Invalid): begin(self.root/'runs/second',1,self.root)
    def test_out_of_order(self):
        with self.assertRaises(Invalid): begin(self.run,3,self.root)
    def test_no_overwrite_existing_run(self):
        with self.assertRaises(Invalid): init_run('test-run',INPUT,self.root)
    def test_path_traversal(self):
        with self.assertRaises(Invalid): run_path('../escape',self.root)
    def event(self,tool,data): return {'tool_name':tool,'tool_input':data,'cwd':str(self.root)}
    def test_guard_worker_own_folder(self):
        begin(self.run,1,self.root)
        self.assertTrue(check(self.event('Write',{'file_path':str(self.run/'01-research/a.md')}),'researcher',self.root))
    def test_guard_cross_folder(self):
        begin(self.run,1,self.root)
        with self.assertRaises(Invalid): check(self.event('Write',{'file_path':str(self.run/'02-spec/a.md')}),'researcher',self.root)
    def test_guard_symlink_escape(self):
        begin(self.run,1,self.root); (self.run/'01-research/escape').symlink_to(self.root.parent,target_is_directory=True)
        with self.assertRaises(Invalid): check(self.event('Write',{'file_path':str(self.run/'01-research/escape/out.md')}),'researcher',self.root)
    def test_guard_readonly_judge(self):
        begin(self.run,1,self.root)
        with self.assertRaises(Invalid): check(self.event('Write',{'file_path':str(self.run/'01-research/a.md')}),'judge',self.root)
    def test_guard_shell_injection(self):
        with self.assertRaises(Invalid): check(self.event('Bash',{'command':'sh scripts/harness status test-run; touch x'}),'global',self.root)
    def test_guard_approval_write(self):
        self.approve_fixture(); s=state(self.run); s['active_stage']=3; write(self.run/'run-state.json',s)
        with self.assertRaises(Invalid): check(self.event('Write',{'file_path':str(self.run/'03-concept/approval.json')}),'concept-designer',self.root)
    def test_guard_no_worker_approval_command(self):
        begin(self.run,1,self.root)
        with self.assertRaises(Invalid): check(self.event('Bash',{'command':'sh scripts/harness approve test-run --message fake'}),'researcher',self.root)
    def test_actual_design_coverage(self):
        self.approve_fixture()
        def change(x):
            x['frames'].pop(); x['manifest']['frames'].pop(); x['source']['manifest_hash']=digest(x['manifest'])
        self.edit('04-design/screens.json',change); self.fails('DESIGN_COVERAGE',4)
    def test_na_is_not_pass(self):
        self.edit('input.json',lambda x:x.update(scope={'asset_registration':False,'sale_application':False,'not_applicable_reason':'무료 학습자료 흐름'}))
        statuses={c['id']:c['status'] for c in self.report()['checks']}
        self.assertEqual(statuses['SELLER_ONLY'],'not_applicable')
    def test_na_requires_reason(self):
        self.edit('input.json',lambda x:x['scope'].update(asset_registration=False)); self.fails('VALIDATION_ERRORS')
    def test_rest_null_node_is_blocked(self):
        with self.assertRaises(Invalid): normalize({'version':'v1','nodes':{'1:1':None}}, {'file_key':'test','frames':[{'screen_id':'one','node_id':'1:1'}],'roles':{}})
    def test_rest_normalization_walks_descendants(self):
        doc={'id':'1:1','type':'FRAME','absoluteBoundingBox':{'width':390,'height':844},'children':[
            {'id':'1:2','type':'RECTANGLE','absoluteBoundingBox':{'width':100,'height':44},'cornerRadius':9999,
             'effects':[{'type':'DROP_SHADOW','visible':True}]}]}
        result=normalize({'version':'v1','nodes':{'1:1':{'document':doc}}},{'file_key':'test','frames':[{'screen_id':'one','node_id':'1:1'}],'roles':{'1:2':'button'}})
        self.assertEqual(len(result['frames'][0]['nodes']),2)
        self.assertEqual(result['frames'][0]['nodes'][1]['effects'][0]['type'],'DROP_SHADOW')
    def test_check_is_readonly(self):
        before={str(p):p.read_bytes() for p in self.run.rglob('*') if p.is_file()}; self.report()
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.run.rglob('*') if p.is_file()})

if __name__=='__main__': unittest.main()

class IntegrationChecks(unittest.TestCase):
    def test_cli_help(self):
        import subprocess
        p=subprocess.run(['sh',str(ROOT/'scripts/harness'),'--help'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('approve',p.stdout)
    def test_cli_missing_run_nonzero(self):
        import subprocess
        p=subprocess.run(['sh',str(ROOT/'scripts/harness'),'check','nonexistent-validation-run'],capture_output=True,text=True)
        self.assertEqual(p.returncode,1,p.stderr)
        self.assertFalse(json.loads(p.stdout)['passed'])
    def test_hook_blocks_shell_directly(self):
        import subprocess
        event={'hook_event_name':'PreToolUse','tool_name':'Bash','tool_input':{'command':'rm -rf anything'},'cwd':str(ROOT)}
        p=subprocess.run(['sh',str(ROOT/'scripts/harness'),'guard','--role','global'],input=json.dumps(event),capture_output=True,text=True)
        self.assertEqual(p.returncode,2)
    def test_settings_valid_and_roles_present(self):
        config=json.loads((ROOT/'.claude/settings.json').read_text())
        self.assertEqual(set(config['hooks']),{'PreToolUse','SubagentStop','UserPromptSubmit'})
        for role in ('researcher','screen-planner','concept-designer','design-builder','judge'):
            content=(ROOT/'.claude/agents'/f'{role}.md').read_text()
            self.assertIn(f'name: {role}',content)
            self.assertIn(f'guard --role {role}',content)
        judge=(ROOT/'.claude/agents/judge.md').read_text()
        self.assertIn('tools: Read, Glob, Grep, Bash',judge)
    def test_live_approval_requires_user_event(self):
        case=HarnessTests(); case.setUp()
        try:
            case.approve_fixture()
            case.edit('input.json',lambda x:x.update(mode='live'))
            # Build receipt-backed live evidence to isolate approval-origin validation.
            for stage,rel in ((3,'03-concept/key-screens.json'),(4,'04-design/screens.json')):
                doc=read(case.run/rel); doc['source']['kind']='figma-rest'; write(case.run/rel,doc)
                s=state(case.run); s.setdefault('collections',{})[str(stage)]={'hash':digest(doc)}; write(case.run/'run-state.json',s)
            from core import fingerprints
            s=state(case.run)
            for i in (1,2,3): s['stages'][str(i)]['checkpoint']=fingerprints(case.run,case.root)[str(i)]
            write(case.run/'run-state.json',s)
            with self.assertRaises(Invalid): approve(case.run,'test-run 컨셉 v1 승인',case.root)
        finally: case.doCleanups()
    def test_rest_fetch_error_is_blocked(self):
        import urllib.error
        from figma_collect import fetch
        with patch('urllib.request.build_opener') as mock:
            mock.return_value.open.side_effect=urllib.error.HTTPError('https://api.figma.com',403,'denied',{},None)
            with self.assertRaisesRegex(Invalid,'HTTP 403'): fetch('test',['1:1'],'fixture-secret')
    def test_rest_unknown_node_role_is_blocked(self):
        doc={'id':'1:1','type':'FRAME','absoluteBoundingBox':{'width':390,'height':844},'children':[
            {'id':'1:2','type':'RECTANGLE','absoluteBoundingBox':{'width':100,'height':44}}]}
        with self.assertRaises(Invalid): normalize({'version':'v1','nodes':{'1:1':{'document':doc}}},{'file_key':'test','frames':[{'screen_id':'one','node_id':'1:1'}],'roles':{}})
