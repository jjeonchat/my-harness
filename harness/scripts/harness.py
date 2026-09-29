#!/usr/bin/env python3
"""CLI/state machine. Exit 0 pass, 1 gate failure, 2 invalid/blocked."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from core import (ROOT, STAGES, ROLES, CONTEXT, Gate, Invalid, approval_valid, digest, evaluate,
                  file_hash, fingerprints, input_data, now, read, require, rules)

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    require(not path.is_symlink(), '심볼릭 링크 대상 쓰기 금지')
    content = value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    fd, temporary=tempfile.mkstemp(prefix='.write-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

def run_path(run_id, root=ROOT):
    require(bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',run_id)), '실행ID: 영문·숫자·밑줄·하이픈, 최대 80자')
    run=root/'runs'/run_id
    require(run.resolve()==root.resolve()/'runs'/run_id, '실행 폴더 심볼릭 링크/경로 이탈 금지')
    return run

@contextmanager
def locked(root):
    # transient process lock lives outside tracked sources and run artifacts
    name=digest(str(root.resolve()))[:24]
    with open(Path(tempfile.gettempdir())/f'huddling-harness-{os.getuid()}-{name}.lock','a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        yield

def state(run):
    return read(run/'run-state.json')

def save_state(run, value):
    value['updated_at']=now(); write(run/'run-state.json',value)

def init_run(run_id, payload, root=ROOT):
    run=run_path(run_id,root)
    require(not run.exists(),'동일 실행ID가 이미 존재합니다.')
    require(all((root.parent/n).is_file() for n in CONTEXT),'원본 컨텍스트 문서 누락')
    rules(root)
    candidate=dict(payload,run_id=run_id,schema_version=1)
    # validate before creating a visible run
    with tempfile.TemporaryDirectory() as tmp:
        probe=Path(tmp)/run_id; probe.mkdir(); write(probe/'input.json',candidate); input_data(probe)
    run.mkdir(parents=True)
    write(run/'input.json',candidate)
    s={'schema_version':1,'run_id':run_id,'active_stage':None,'dispatch':False,'halted':False,
       'stages':{str(i):{'status':'pending','checkpoint':None} for i in range(1,6)},
       'repairs':{},'last_failures':[],'created_at':now()}
    save_state(run,s)
    return {'run_id':run_id,'mode':candidate['mode'],'next_stage':1}

def invalidate(s, first):
    for i in range(first,6): s['stages'][str(i)]={'status':'pending','checkpoint':None}

def changed_stage(run,s,root=ROOT):
    fp=fingerprints(run,root)
    for i in range(1,5):
        checkpoint=s['stages'][str(i)]['checkpoint']
        if checkpoint and checkpoint!=fp[str(i)]: return i
    return None

def resume(run,root=ROOT):
    s=state(run)
    require(not s['dispatch'], '작업자가 실행 중입니다. 종료를 확인한 뒤 release를 사용하세요.')
    changed=changed_stage(run,s,root)
    if changed:
        invalidate(s,changed)
        if changed<=3 and (run/'03-concept/approval.json').exists():
            old=read(run/'03-concept/approval.json'); old['invalidated_at']=now(); old['checkpoint']='invalidated'
            write(run/'03-concept/approval.json',old)
    if s['active_stage']:
        s['stages'][str(s['active_stage'])]['status']='pending'
        s['active_stage']=None
    save_state(run,s)
    return {'changed_from':changed,'halted':s['halted'],'repairs':s['repairs'], 'state':s}

def begin(run, stage, root=ROOT):
    s=state(run)
    require(not s['halted'], '재시도 한도 도달. 원인을 해결하고 별도의 새 실행을 시작해야 합니다.')
    require(s['active_stage'] is None and not s['dispatch'], '이미 작업자가 활성 상태입니다.')
    require(not s['last_failures'], '실패 항목별 repair로 복귀 단계를 예약하세요.')
    for other in (root/'runs').glob('*/run-state.json'):
        o=read(other)
        require(other.parent==run or (o['active_stage'] is None and not o.get('dispatch')), '다른 실행의 작업자가 활성 상태입니다.')
    require(changed_stage(run,s,root) is None,'입력/산출물 변경 감지. 먼저 resume 하세요.')
    for i in range(1,stage): require(s['stages'][str(i)]['status']=='complete', f'{i}단계 미완료')
    require(s['stages'][str(stage)]['status']!='complete','이미 완료된 단계입니다.')
    if stage>=4: require(approval_valid(run,root),'현재 컨셉 버전의 명시적 승인이 필요합니다.')
    s['active_stage']=stage; s['stages'][str(stage)]['status']='running'; save_state(run,s)
    return {'stage':stage,'role':next((r for r,i in ROLES.items() if i==stage),'judge')}

def report_markdown(report):
    lines=['# 검사 결과', '', f"실행: {report['run_id']} / 모드: {report['mode']}",
           f"판정: {'통과' if report['passed'] else '진행 차단'}", '',
           '| 검사 ID | 상태 | 예상 | 실제·증거 | 실패 원인 | 복귀 |','|---|---|---|---|---|---|']
    for c in report['checks']:
        fields=[c['id'],c['status'],c['expected'],c['actual'],c['reason'],str(c['return_stage'])]
        lines.append('| '+' | '.join(str(x).replace('|','\\|').replace('\n',' ') for x in fields)+' |')
    return '\n'.join(lines)+'\n'

def record_report(run,report):
    write(run/'05-review/check-results.json',report)
    write(run/'05-review/review.md',report_markdown(report))

def finish(run, root=ROOT):
    s=state(run); stage=s['active_stage']
    require(stage is not None, '활성 단계 없음')
    require(not s['dispatch'], '작업자 종료 후 finish 하세요.')
    report=evaluate(run,root,stage)
    # Direct changes to earlier stages invalidate the dependency chain.
    changed=changed_stage(run,s,root)
    if changed is not None and changed<stage:
        report['checks'].append({'id':'DEPENDENCIES','status':'blocked','expected':'이전 완료 단계 해시 일치',
            'actual':f'{changed}단계 변경','evidence':f'{changed}단계 변경','reason':'상위 단계가 변경됨','return_stage':changed,'source':'harness-R4-artifacts.md'})
        report['passed']=False
    failures=[x for x in report['checks'] if x['status'] not in ('pass','not_applicable')]
    s['active_stage']=None
    s['last_failures']=failures
    if report['passed']:
        s['stages'][str(stage)]={'status':'complete','checkpoint':fingerprints(run,root).get(str(stage))}
        for check in report['checks']:
            if check['status']=='pass': s['repairs'].pop(check['id'],None)
    else:
        s['stages'][str(stage)]['status']='blocked'
        s['halted']=any(s['repairs'].get(f['id'],0)>=rules(root)['limits']['max_repairs'] for f in failures)
    save_state(run,s); record_report(run,report)
    return report

def approve(run, message, root=ROOT):
    s=state(run)
    require(s['active_stage'] is None and not s['dispatch'], '작업 중에는 승인할 수 없습니다.')
    require(not s['halted'], '중단된 실행')
    require(s['stages']['3']['status']=='complete','키스크린 검사가 완료되지 않았습니다.')
    require(changed_stage(run,s,root) is None,'변경 감지: resume 및 재검사 필요')
    concept=read(run/'03-concept/key-screens.json')
    require(message==f"{run.name} 컨셉 {concept['concept_version']} 승인", '실행ID·버전이 포함된 정확한 사용자 승인 메시지가 필요합니다.')
    if input_data(run)['mode']=='live':
        intent=s.get('user_approval_intent',{})
        require(intent.get('message')==message and intent.get('checkpoint')==fingerprints(run,root)['3'],
                'Claude Code의 실제 사용자 승인 메시지 이벤트가 필요합니다. 에이전트가 승인을 만들 수 없습니다.')
    require(evaluate(run,root,3)['passed'], '컨셉 게이트 미통과')
    approval={'run_id':run.name,'concept_version':concept['concept_version'],'checkpoint':fingerprints(run,root)['3'],
              'actor':'user','message':message,'approved_at':now()}
    write(run/'03-concept/approval.json',approval)
    s.pop('user_approval_intent',None); save_state(run,s)
    return approval

def repair(run,rule_id,root=ROOT):
    s=state(run)
    require(not s['halted'] and s['active_stage'] is None and not s['dispatch'],'활성/중단 상태에서는 수정 예약 불가')
    failures=[f for f in s['last_failures'] if f['id']==rule_id]
    require(bool(failures),'최근 실패한 검사 ID만 수정 가능')
    count=s['repairs'].get(rule_id,0)
    require(count<rules(root)['limits']['max_repairs'],'자동 수정 2회 소진')
    s['repairs'][rule_id]=count+1
    target=min(f['return_stage'] for f in failures)
    invalidate(s,target)
    # Consume failure authorization: cannot reserve both attempts without running a check.
    s['last_failures']=[f for f in s['last_failures'] if f['id']!=rule_id]
    save_state(run,s)
    return {'rule_id':rule_id,'repair_attempt':count+1,'return_stage':target}

def review(run,root=ROOT):
    report=evaluate(run,root,5)
    record_report(run,report)
    return report

def main():
    parser=argparse.ArgumentParser(description='허들링 하네스 — 외부 패키지 없는 실행/판정 CLI')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('init'); p.add_argument('run_id'); group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--input'); group.add_argument('--json')
    for name in ('status','resume','finish','review','release'):
        p=sub.add_parser(name); p.add_argument('run_id')
    p=sub.add_parser('begin'); p.add_argument('run_id'); p.add_argument('stage',type=int,choices=range(1,6))
    p=sub.add_parser('approve'); p.add_argument('run_id'); p.add_argument('--message',required=True)
    p=sub.add_parser('repair'); p.add_argument('run_id'); p.add_argument('rule_id')
    p=sub.add_parser('check'); p.add_argument('run_id'); p.add_argument('--through',type=int,choices=range(1,6),default=5)
    p=sub.add_parser('collect'); p.add_argument('run_id'); p.add_argument('stage',type=int,choices=(3,4))
    p=sub.add_parser('guard'); p.add_argument('--role',default='global')
    args=parser.parse_args()
    if args.command=='guard':
        from guard import handle
        return handle(args.role)
    try:
        if args.command=='check':
            result=evaluate(run_path(args.run_id),through=args.through)
        else:
            with locked(ROOT):
                if args.command=='init': result=init_run(args.run_id,read(Path(args.input)) if args.input else json.loads(args.json))
                else:
                    run=run_path(args.run_id)
                    require(run.is_dir(),'해당 실행이 없습니다.')
                    if args.command=='status': result=state(run)
                    elif args.command=='resume': result=resume(run)
                    elif args.command=='begin': result=begin(run,args.stage)
                    elif args.command=='finish': result=finish(run)
                    elif args.command=='approve': result=approve(run,args.message)
                    elif args.command=='repair': result=repair(run,args.rule_id)
                    elif args.command=='review': result=review(run)
                    elif args.command=='collect':
                        from figma_collect import collect
                        result=collect(run,args.stage)
                    elif args.command=='release':
                        s=state(run); s['dispatch']=False; save_state(run,s)
                        result={'released':True,'notice':'작업자가 실제로 종료됐는지 확인한 후에만 사용하세요.'}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 1 if result.get('passed') is False else 0
    except (Invalid, OSError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({'error':str(exc),'status':'blocked'},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=='__main__':
    sys.exit(main())
