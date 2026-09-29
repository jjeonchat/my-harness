#!/usr/bin/env python3
"""Run automated checks and keep unperformed external checks explicitly separate."""
import json
from pathlib import Path
import sys
import unittest
from datetime import datetime, timezone
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts')); sys.path.insert(0,str(ROOT/'tests'))

class Recorded(unittest.TextTestResult):
    def __init__(self,*args,**kwargs): super().__init__(*args,**kwargs); self.records=[]
    def addSuccess(self,test):
        super().addSuccess(test); self.records.append({'test':test.id(),'expected':'asserted contract holds','actual':'pass','status':'pass'})
    def addFailure(self,test,err):
        super().addFailure(test,err); self.records.append({'test':test.id(),'expected':'asserted contract holds','actual':self._exc_info_to_string(err,test),'status':'fail'})
    def addError(self,test,err):
        super().addError(test,err); self.records.append({'test':test.id(),'expected':'asserted contract holds','actual':self._exc_info_to_string(err,test),'status':'error'})

suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
result=unittest.TextTestRunner(verbosity=1,resultclass=Recorded).run(suite)
report={'generated_at':datetime.now(timezone.utc).isoformat(),'automated':{'count':result.testsRun,'passed':result.wasSuccessful(),'tests':result.records},
        'external':{'live_figma':{'status':'not_run','reason':'검증할 실제 Figma 파일/프레임 URL이 제공되지 않음'},
                    'claude_code_runtime':{'status':'not_run','reason':'현재 환경에서 claude 실행 파일을 찾지 못함'}},
        'overall_validation_complete':False}
out=ROOT/'validation'; out.mkdir(exist_ok=True)
(out/'test-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
lines=['# 구현 검증 결과','',f"생성: {report['generated_at']}",'',
       f"- 자동 테스트: {result.testsRun}개 / {'통과' if result.wasSuccessful() else '실패'}",
       '- 실제 Figma 검증: 미실행 — 대상 URL 미제공',
       '- Claude Code 세션 검증: 미실행 — 실행 파일 미확인',
       '- 전체 R8 검증 완료: 아니오','',
       '가상 데이터 테스트는 실제 Figma 및 Claude Code 실행 성공을 의미하지 않는다.','',
       '| 테스트 | 결과 |','|---|---|']
lines += [f"| {r['test'].split('.')[-1]} | {r['status']} |" for r in result.records]
(out/'README.md').write_text('\n'.join(lines)+'\n')
sys.exit(0 if result.wasSuccessful() else 1)
