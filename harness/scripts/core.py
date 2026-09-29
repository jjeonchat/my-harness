"""Deterministic, dependency-free gates. Evaluators never write files."""
from __future__ import annotations
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = {
    1: ('01-research', ['references.json', 'analysis.md']),
    2: ('02-spec', ['screen-spec.md', 'screens.json']),
    3: ('03-concept', ['key-screens.json']),
    4: ('04-design', ['tokens.json', 'components.json', 'screens.json']),
}
ROLES = {'researcher': 1, 'screen-planner': 2, 'concept-designer': 3, 'design-builder': 4}
CONTEXT = ['prd.md', 'design.md', 'story-service.md', 'story-workflow.md']
NODE_ROLES = {'screen','button','badge','toggle','input','content-card','app-icon',
              'featured-pricing','text','layout','media','other'}

class Invalid(ValueError):
    pass

def require(condition, message):
    if not condition:
        raise Invalid(message)

def now():
    return datetime.now(timezone.utc).isoformat()

def read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda x: (_ for _ in ()).throw(Invalid('non-finite number')))
    except (OSError, ValueError) as exc:
        raise Invalid(f'{path.name}: 읽기 실패 또는 JSON 오류') from exc

def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def object_(x, keys, label):
    require(isinstance(x, dict), f'{label}: 객체 필요')
    require(all(k in x for k in keys), f'{label}: 필수 필드 {keys}')
    return x

def text(x, label):
    require(isinstance(x, str) and bool(x.strip()), f'{label}: 비어 있지 않은 문자열 필요')
    return x

def list_(x, label, nonempty=True):
    require(isinstance(x, list) and (bool(x) or not nonempty), f'{label}: 배열 필요')
    return x

def number(x, label):
    require(type(x) in (int, float) and math.isfinite(x), f'{label}: 유한 숫자 필요')
    return x

def boolean(x, label):
    require(type(x) is bool, f'{label}: true/false 필요')
    return x

def unique(values, label):
    require(len(values) == len(set(values)), f'{label}: 중복 ID')

def rules(root=ROOT):
    result = read(root / 'rules.json')
    object_(result, ['schema_version','limits','design','service','checks'], 'rules')
    require(result['schema_version'] == 1, '지원하지 않는 rules 버전')
    unique([x['id'] for x in result['checks']], '규칙')
    return result

def input_data(run):
    data = object_(read(run/'input.json'), ['schema_version','run_id','flow','screen_ids','references','scope','mode'], 'input')
    require(data['schema_version'] == 1, '지원하지 않는 input 버전')
    require(data['run_id'] == run.name, '실행 ID 불일치')
    text(data['flow'], 'flow')
    ids = list_(data['screen_ids'], 'screen_ids')
    for s in ids: text(s, 'screen_id')
    unique(ids, 'screen_ids')
    list_(data['references'], 'references')
    for ref in data['references']: text(ref, 'reference')
    require(data['mode'] in ('live','fixture'), 'mode: live 또는 fixture')
    scope = object_(data['scope'], ['asset_registration','sale_application','not_applicable_reason'], 'scope')
    for k in ('asset_registration','sale_application'): boolean(scope[k], k)
    if not all(scope[k] for k in ('asset_registration','sale_application')):
        text(scope['not_applicable_reason'], '해당 없음 근거')
    return data

def fingerprints(run, root=ROOT):
    shared = {'input':file_hash(run/'input.json'), 'rules':file_hash(root/'rules.json'),
              'context': {n:file_hash(root.parent/n) for n in CONTEXT}}
    result = {}
    for stage, (folder, names) in STAGES.items():
        result[str(stage)] = digest({'shared':shared, 'files':{n:file_hash(run/folder/n) for n in names},
                                    'upstream':dict(result)})
    return result

def approval_valid(run, root=ROOT):
    try:
        approval = read(run/'03-concept/approval.json')
        concept = read(run/'03-concept/key-screens.json')
        expected = f"{run.name} 컨셉 {concept['concept_version']} 승인"
        return (approval['run_id'] == run.name and approval['message'] == expected and
                approval['concept_version'] == concept['concept_version'] and
                approval['checkpoint'] == fingerprints(run, root)['3'] and
                bool(approval['approved_at']) and approval['actor'] == 'user')
    except (Invalid, KeyError, TypeError):
        return False

class Gate:
    def __init__(self, run, root=ROOT):
        self.run, self.root, self.results = run, root, []
        self.rules = rules(root)
        self.catalog = {x['id']:x for x in self.rules['checks']}
        self.input = input_data(run)
    def add(self, rule, ok, evidence, back, status=None):
        item = self.catalog[rule]
        status = status or ('pass' if ok else 'fail')
        self.results.append({'id':rule,'status':status,'expected':item['condition'],
            'actual':evidence,'evidence':evidence,'reason':'' if status in ('pass','not_applicable') else evidence,
            'return_stage':back,'source':item['source']})
    def na(self, rule, back):
        self.add(rule, True, self.input['scope']['not_applicable_reason'], back, 'not_applicable')
    def artifacts(self, stage):
        folder, names = STAGES[stage]
        for name in names:
            path = self.run/folder/name
            require(path.resolve().is_relative_to(self.run.resolve()), f'{name}: 실행 폴더 이탈')
            require(path.is_file() and path.stat().st_size > 0, f'{folder}/{name}: 누락 또는 빈 파일')
            require(not path.is_symlink(), f'{name}: 심볼릭 링크 금지')
            if name.endswith('.json'): read(path)
            else: text(path.read_text(), name)
        self.add('ARTIFACTS', True, ', '.join(f'{folder}/{n}' for n in names), stage)
    def research(self):
        refs = list_(read(self.run/'01-research/references.json'), 'references')
        for ref in refs:
            object_(ref, ['source','accessible','summary'], 'reference')
            text(ref['source'], 'source'); boolean(ref['accessible'], 'accessible')
            text(ref['summary'], 'summary 또는 접근 실패 사유')
        self.add('REFERENCES', any(r['accessible'] for r in refs),
                 f"접근 가능 자료 {sum(r['accessible'] for r in refs)}개", 1)
    def spec(self):
        spec = object_(read(self.run/'02-spec/screens.json'), ['screens','service'], 'screen specification')
        screens = list_(spec['screens'], 'screens')
        for screen in screens:
            object_(screen,['id','title'], 'screen'); text(screen['id'],'id'); text(screen['title'],'title')
        ids = [s['id'] for s in screens]; unique(ids,'화면')
        self.add('SPEC_COVERAGE', set(ids)==set(self.input['screen_ids']), f'설계 화면 IDs: {ids}', 2)
        service, scope, policy = spec['service'], self.input['scope'], self.rules['service']
        object_(service, [], 'service')
        active = scope['asset_registration'] or scope['sale_application']
        if scope['asset_registration']:
            object_(service,['default_visibility'], 'service')
            self.add('PRIVATE_DEFAULT', service['default_visibility']==policy['private_default'],
                     f"default_visibility={service['default_visibility']}", 2)
        else: self.na('PRIVATE_DEFAULT', 2)
        if active:
            object_(service,['transitions','states'], 'service')
            transitions = list_(service['transitions'], 'transitions')
            states = list_(service['states'], 'states')
            for s in states: text(s, 'state')
            for t in transitions:
                object_(t,['from','to','user_selected','reviewed_by_operator'], 'transition')
                text(t['from'],'from'); text(t['to'],'to')
                boolean(t['user_selected'],'user_selected'); boolean(t['reviewed_by_operator'],'reviewed_by_operator')
            bad = [t for t in transitions if t['to'] in policy['public_targets'] and not t['user_selected']]
            self.add('EXPLICIT_VISIBILITY', not bad, f'사용자 선택 없는 전이 {len(bad)}개', 2)
            state_set = set(states) | {t[k] for t in transitions for k in ('from','to')}
            bad_states = sorted(state_set & set(policy['forbidden_states']))
            self.add('MVP_STATES', not bad_states, f'금지 상태: {bad_states}', 2)
        else:
            transitions = []
            self.na('EXPLICIT_VISIBILITY',2); self.na('MVP_STATES',2)
        if scope['sale_application']:
            object_(service,['sale_roles'], 'service')
            roles = list_(service['sale_roles'],'sale_roles')
            for role in roles: text(role,'role')
            self.add('SELLER_ONLY', roles == policy['sale_roles'], f'판매 신청 역할: {roles}', 2)
            approvals = [t for t in transitions if t['to']=='approved']
            self.add('REVIEW_REQUIRED', bool(approvals) and all(t['reviewed_by_operator'] for t in approvals),
                     f'승인 전이 {len(approvals)}개; 운영자 검수 조건 {[t["reviewed_by_operator"] for t in approvals]}', 2)
        else:
            self.na('SELLER_ONLY',2); self.na('REVIEW_REQUIRED',2)
    def snapshot(self, stage):
        rel = '03-concept/key-screens.json' if stage==3 else '04-design/screens.json'
        doc = object_(read(self.run/rel), ['source','frames','manifest'], rel)
        if stage==3: text(doc.get('concept_version'), 'concept_version')
        src = object_(doc['source'], ['kind','file_key','version','collected_at','complete','manifest_hash'], 'source')
        for k in ('file_key','version','collected_at','manifest_hash'): text(src[k], k)
        require(src['complete'] is True, '불완전한 Figma 수집')
        require(src['kind']=='figma-rest' or (src['kind']=='fixture' and self.input['mode']=='fixture'), '실제 Figma 증거 필요')
        if self.input['mode']=='live':
            receipt = read(self.run/'run-state.json').get('collections',{}).get(str(stage),{})
            require(receipt.get('hash')==digest(doc), '직접 수집 기록이 없거나 수집 후 증거가 변경됨')
        require(src['manifest_hash']==digest(doc['manifest']), '수집 후 manifest 변경됨')
        frames = list_(doc['frames'], 'frames')
        manifest = object_(doc['manifest'],['file_key','frames','roles'], 'manifest')
        require(manifest['file_key']==src['file_key'], 'Figma 파일 불일치')
        expected = list_(manifest['frames'], 'manifest frames')
        require(len(expected)==len(frames), 'Figma 루트 일부 누락')
        pairs = []
        nodes = []
        for frame in frames:
            object_(frame,['screen_id','id','width','height','nodes'], 'frame')
            text(frame['screen_id'],'screen_id'); text(frame['id'],'node ID')
            number(frame['width'],'width'); number(frame['height'],'height')
            children = list_(frame['nodes'], 'collected nodes')
            require(any(n.get('id')==frame['id'] for n in children), '루트 노드 누락')
            pairs.append((frame['screen_id'],frame['id']))
            nodes += children
        unique([p[0] for p in pairs], 'frame screen IDs'); unique([p[1] for p in pairs], 'frame node IDs')
        require(set(pairs)=={(x['screen_id'],x['node_id']) for x in expected}, 'manifest와 수집 프레임 불일치')
        self.add('FIGMA_EVIDENCE', True, f'{rel}: {src["kind"]}, version={src["version"]}, frames={len(frames)}', stage)
        if stage==3:
            lim=self.rules['limits']
            self.add('KEY_COUNT', lim['key_screens_min']<=len(frames)<=lim['key_screens_max'], f'키스크린 {len(frames)}개', 3)
            self.add('FRAME_SIZE', all(f['width']==lim['width'] and f['height']==lim['height'] for f in frames),
                     str([(f['id'],f['width'],f['height']) for f in frames]), 3)
            require(set(p[0] for p in pairs)<=set(self.input['screen_ids']), '합의하지 않은 키스크린 ID')
        else:
            self.add('DESIGN_COVERAGE', set(p[0] for p in pairs)==set(self.input['screen_ids']), f'완성 화면 IDs: {[p[0] for p in pairs]}', 2)
        self.styles(nodes, stage)
    def styles(self, nodes, stage):
        design=self.rules['design']
        violations = {k:[] for k in ('NO_SHADOW','FEATURED_FILL','LETTER_SPACING','TEXT_CASE','RADIUS')}
        unique([n.get('id') for n in nodes], '노드')
        for n in nodes:
            object_(n,['id','type','role','width','height','effects','fills','corners','text_runs'], 'node')
            text(n['id'],'node ID'); text(n['type'],'node type')
            require(n['role'] in NODE_ROLES, '분류되지 않은 노드: '+n['id'])
            number(n['width'],'node width'); number(n['height'],'node height')
            for e in list_(n['effects'],'effects',False):
                object_(e,['type','visible'],'effect'); boolean(e['visible'],'effect visible')
                if e['visible'] and e['type'] in design['shadow_types']: violations['NO_SHADOW'].append(n['id'])
            fills=list_(n['fills'],'fills',False)
            if n['role']=='featured-pricing':
                if not (len(fills)==1 and fills[0].get('type')=='SOLID' and fills[0].get('color')==design['featured_fill'] and fills[0].get('opacity')==1):
                    violations['FEATURED_FILL'].append(n['id'])
            runs=list_(n['text_runs'],'text_runs',False)
            if n['type']=='TEXT': require(bool(runs), '텍스트 스타일 증거 누락')
            for t in runs:
                object_(t,['letter_spacing','text_case'],'text run')
                number(t['letter_spacing'],'letter spacing'); text(t['text_case'],'text case')
                if t['letter_spacing']!=design['letter_spacing']: violations['LETTER_SPACING'].append(n['id'])
                if t['text_case'] in design['forbidden_text_case']: violations['TEXT_CASE'].append(n['id'])
            expected=design['radii'].get(n['role'])
            if n['role']=='app-icon':
                require(n['width']==n['height'] and n['width']>0, 'app-icon: 정사각형 크기 필요 (30% 기준)')
                expected=n['width']*design['icon_radius_ratio']
            if expected is not None:
                corners=list_(n['corners'],'corners'); require(len(corners)==4,'4개 모서리 필요')
                for c in corners: number(c,'corner')
                if not all(math.isclose(c,expected,abs_tol=1e-6) for c in corners): violations['RADIUS'].append(n['id'])
        for rule, ids in violations.items():
            self.add(rule, not ids, f'위반 노드 {sorted(set(ids))}', stage)
    def assets(self):
        tokens=object_(read(self.run/'04-design/tokens.json'), ['tokens'], 'tokens')
        comps=object_(read(self.run/'04-design/components.json'), ['components'], 'components')
        for t in list_(tokens['tokens'],'tokens'):
            object_(t,['id','name','value','figma_variable_id'],'token')
            for k in ('id','name','figma_variable_id'): text(t[k],k)
        for c in list_(comps['components'],'components'):
            object_(c,['id','name','figma_node_id'],'component')
            for k in ('id','name','figma_node_id'): text(c[k],k)
    def evaluate(self, through=5, require_approval=True):
        for stage in range(1, min(through,4)+1):
            try:
                self.artifacts(stage)
                {1:self.research,2:self.spec,3:lambda:self.snapshot(3),4:lambda:(self.assets(),self.snapshot(4))}[stage]()
            except (Invalid, KeyError, TypeError, IndexError) as exc:
                self.add('VALIDATION_ERRORS', False, f'단계 {stage}: {exc}', stage, 'blocked')
        if through>=4 and require_approval:
            self.add('APPROVAL', approval_valid(self.run,self.root), '현재 실행·컨셉·입력·규칙에 대한 사용자 승인', 3)
        return self.results

def evaluate(run, root=ROOT, through=5):
    try:
        results=Gate(run,root).evaluate(through)
    except (Invalid, KeyError, TypeError) as exc:
        results=[{'id':'VALIDATION_ERRORS','status':'blocked','expected':'유효한 입력 및 규칙',
                  'actual':str(exc),'evidence':str(exc),'reason':str(exc),'return_stage':1,'source':'harness-R8-validation.md'}]
    try:
        mode=read(run/'input.json').get('mode')
    except (Invalid, AttributeError):
        mode=None
    return {'run_id':run.name,'checked_at':now(),'mode':mode,
            'passed':all(r['status'] in ('pass','not_applicable') for r in results), 'checks':results}
