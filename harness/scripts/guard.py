"""Claude PreToolUse guard. Restricted shell grammar, folder and stage checks."""
import json
from pathlib import Path
import shlex
import sys
from core import ROOT, ROLES, STAGES, Invalid, read, require

def active(root):
    matches=[]
    for p in (root/'runs').glob('*/run-state.json'):
        s=read(p)
        if s.get('active_stage') is not None: matches.append((p.parent,s))
    require(len(matches)<=1,'동시에 두 개 이상의 실행이 활성화됨')
    return matches[0] if matches else (None,None)

def check(event, role, root=ROOT):
    tool=event.get('tool_name',''); data=event.get('tool_input',{})
    run,s=active(root)
    if tool in ('Write','Edit','MultiEdit','NotebookEdit'):
        require(tool in ('Write','Edit'),'이 하네스는 Write/Edit만 허용합니다.')
        raw=data.get('file_path'); require(isinstance(raw,str),'file_path 누락')
        path=Path(raw)
        if not path.is_absolute(): path=Path(event.get('cwd',root))/path
        path=path.resolve()
        require(run is not None,'활성 실행 없음')
        if role=='judge': raise Invalid('판정자는 읽기 전용입니다.')
        if role in ROLES:
            stage=ROLES[role]
            require(s['active_stage']==stage,'담당 단계가 활성 상태가 아닙니다.')
            folder=(run/STAGES[stage][0]).resolve()
            require(folder.is_relative_to(run.resolve()),'심볼릭 링크 폴더 이탈')
            require(path.is_relative_to(folder),'담당 폴더 밖 편집 금지')
            require(path != (run/'03-concept/approval.json').resolve(),'작업자는 승인을 기록할 수 없습니다.')
        else:
            # Global hook also runs inside workers; permit only active worker folder.
            # Orchestrator's trusted state/report writes go through the CLI, not Write/Edit.
            stage=s['active_stage']
            require(stage in STAGES,'판정 단계에는 직접 쓰기 금지')
            require((run/STAGES[stage][0]).resolve().is_relative_to(run.resolve()),'심볼릭 링크 폴더 이탈')
            require(path.is_relative_to((run/STAGES[stage][0]).resolve()),'활성 작업 폴더 밖 편집 금지')
            require(path != (run/'03-concept/approval.json').resolve(),'승인은 approve 명령으로만 기록합니다.')
    elif tool=='Bash':
        command=data.get('command','')
        require(isinstance(command,str) and not any(c in command for c in ('\n','\r',';','|','&','`','$','>','<')),'복합 셸 명령 금지')
        argv=shlex.split(command)
        require(len(argv)>=3 and argv[:2]==['sh','scripts/harness'],'sh scripts/harness 명령만 허용')
        cmd=argv[2]
        permitted={'init','status','resume','begin','finish','approve','repair','check','review','release','--help'}
        if role in ('concept-designer','design-builder'): permitted={'collect'}
        elif role=='judge': permitted={'check'}
        elif role in ROLES: permitted=set()
        elif cmd=='collect': permitted.add('collect')
        require(cmd in permitted,'이 역할에서 허용하지 않는 명령')
        if role in ROLES or role=='judge':
            require(run is not None and len(argv)>3 and argv[3]==run.name,'다른 실행 접근 금지')
        if cmd=='collect':
            require(run is not None and len(argv)==5 and argv[3]==run.name and argv[4]==str(s['active_stage']),'현재 단계의 수집만 허용')
    elif tool in ('Agent','Task'):
        require(role=='global','작업자는 하위 작업자를 생성할 수 없습니다.')
        require(run is not None and not s.get('dispatch'),'활성 작업자가 없거나 이미 호출됨')
        expected=next((r for r,n in ROLES.items() if n==s['active_stage']),'judge')
        require(data.get('subagent_type')==expected,'현재 단계에 배정된 에이전트만 호출 가능')
        require(not data.get('run_in_background',False),'작업자는 순차 전경 실행만 허용')
    elif tool.startswith('mcp__'):
        # Remote code execution cannot be proven read-only by string matching.
        # MCP reads and design writes are subject to host permission review and role instructions.
        require(role!='judge','판정자는 원격 변경 가능 도구를 사용하지 않습니다. 수집된 증거와 check만 사용하세요.')
        require('figma' in tool.lower(),'Figma 외 MCP 도구는 이 하네스의 작업 범위에 없습니다.')
        require(s is not None and s['active_stage'] in (3,4),'Figma 도구는 디자인 단계에서만 사용합니다.')
        if role in ROLES: require(role in ('concept-designer','design-builder'),'이 역할은 MCP 호출을 사용하지 않습니다.')
    elif tool not in ('Read','Glob','Grep','WebFetch','WebSearch','AskUserQuestion','ToolSearch'):
        raise Invalid('등록되지 않은 도구입니다. 권한을 임의로 확대하지 마세요.')
    return True

def handle(role):
    try:
        event=json.load(sys.stdin)
        if event.get('hook_event_name')=='UserPromptSubmit':
            import re
            from harness import locked, run_path, state, save_state
            from core import fingerprints
            message=event.get('prompt','').strip()
            match=re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_-]{0,79}) 컨셉 (\S+) 승인',message)
            if match:
                with locked(ROOT):
                    run=run_path(match[1])
                    if run.is_dir():
                        s=state(run)
                        if s['stages']['3']['status']=='complete':
                            s['user_approval_intent']={'message':message,'checkpoint':fingerprints(run)['3']}
                            save_state(run,s)
            return 0
        if event.get('hook_event_name')=='SubagentStop':
            from harness import locked, save_state
            with locked(ROOT):
                run,s=active(ROOT)
                if run is not None and s.get('dispatch'):
                    s['dispatch']=False; save_state(run,s)
            return 0
        check(event,role)
        if role=='global' and event.get('tool_name') in ('Agent','Task'):
            from harness import locked, save_state
            with locked(ROOT):
                check(event,role)
                run,s=active(ROOT); s['dispatch']=True; save_state(run,s)
        return 0
    except (Invalid,ValueError,KeyError,TypeError,OSError) as exc:
        print('하네스 경계 위반: '+str(exc),file=sys.stderr)
        return 2
