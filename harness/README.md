# 허들링 Claude Code 하네스

R2~R8 합의에 따라 만든 로컬 하네스다. 문서와 실행 엔진을 구분한다.
허들링 앱 자체를 구현하거나 기존 GitHub 보류 요청을 재개하지 않는다.

## 구성

- CLAUDE.md: 승인된 오케스트레이터 지침
- .claude/agents/: 리서처, 화면 설계자, 컨셉 디자이너, 디자인 확장자, 읽기 전용 판정자
- .claude/settings.json: 도구 경계, 순차 배정, 사용자 승인 메시지 훅
- rules.json: 기계 판정 규칙의 단일 기준
- scripts/: Python 표준 라이브러리로 구현한 실행·수집·판정·권한 검사
- schemas.md: 작업자가 읽는 구조화 산출물 계약
- tests/: 실제 서비스 데이터와 분리된 가상 검증 사례
- validation/: 실행한 구현 테스트 결과와 외부 검증 상태
- runs/: 실행별 자료. Git에서는 제외한다.

## 준비

Python 3.10 이상, Claude Code, 읽기 가능한 상위 폴더의 컨텍스트 문서가 필요하다.
쉘 런처는 HARNESS_PYTHON, 설치된 Codex Python, python3 순서로 선택한다.
외부 Python 패키지 설치는 필요하지 않다.

Figma 제작에는 Claude Code에 연결된 Figma 도구가 필요하다. 서버별 도구 이름은 설치 환경을 따른다.
실측 수집기는 Figma REST API를 사용하며 `file_content:read` 권한의 토큰을
`FIGMA_ACCESS_TOKEN` 환경변수에서만 읽는다. 토큰을 채팅·소스·명령 인자에 넣지 않는다.
환경변수를 준비한 터미널에서 Claude Code를 시작해야 하위 수집기에 전달된다.

```sh
cd harness
claude
```

작업 폴더를 신뢰해야 Claude Code의 프로젝트 훅이 실행된다. 이 환경에서는 Claude Code 실행 파일이
확인되지 않아 실제 Claude 세션의 훅 동작은 아직 검증하지 못했다.

## 사용자 요청

- `내 자산 등록 → 판매 신청 디자인 시작`
- `asset-flow-001 이어서 진행`
- `asset-flow-001 컨셉 v1 승인`
- `asset-flow-001 검토`

시작 시 실제 레퍼런스와 Figma 파일/영역을 지정한다. 불명확하면 확인한다.
자연어 요청의 의도 해석은 CLAUDE.md와 에이전트 설명이 맡고, 상태 전환은 아래 CLI가 수행한다.

## 오케스트레이터 명령

```sh
sh scripts/harness --help
sh scripts/harness init asset-flow-001 --input examples/input.json
sh scripts/harness begin asset-flow-001 1
# researcher를 전경으로 1개 호출한다. 이후 호출이 반환되면:
sh scripts/harness finish asset-flow-001
sh scripts/harness begin asset-flow-001 2
# screen-planner 호출 및 반환 후 finish, 같은 방식으로 3단계 진행
```

examples/input.json은 템플릿이다. 실제 자료로 교체한 별도 입력 파일을 사용하거나,
`init <ID> --json '<입력 JSON>'`으로 메모리에서 준비한 입력을 전달한다.
실행ID는 영문·숫자·하이픈·밑줄만 허용한다. 기존 실행을 덮어쓰지 않는다.

컨셉 디자이너는 manifest를 작성하고, 원격 프레임을 만든 후 다음을 실행한다.

```sh
sh scripts/harness collect asset-flow-001 3
```

수집된 모든 노드의 역할 매핑이 필요하다. Figma 원격 디자인의 쓰기는 연결 도구가 담당하고,
collect는 읽기만 한다. begin/finish와 호출 훅이 동시에 활성화되는 작업자를 1개로 제한한다.

3단계 finish 통과 뒤 사용자가 Claude Code에 정확히 `asset-flow-001 컨셉 v1 승인`을 입력한다.
사용자 메시지 훅의 기록이 있어야 오케스트레이터가 다음 명령으로 승인할 수 있다.

```sh
sh scripts/harness approve asset-flow-001 --message 'asset-flow-001 컨셉 v1 승인'
sh scripts/harness begin asset-flow-001 4
# design-builder가 작업하고 collect <ID> 4 실행, 반환 후 finish
sh scripts/harness begin asset-flow-001 5
# judge가 check를 수행하고 반환, 오케스트레이터가 finish
```

판정자는 `check`만 사용한다. `check`는 파일을 쓰지 않는다.
오케스트레이터의 `finish`와 `review`가 JSON/Markdown 결과를 05-review/에 저장한다.
`review`는 기계 검사 보고서만 갱신하며 파이프라인의 단계를 완료 처리하지 않는다.

```sh
sh scripts/harness check asset-flow-001 --through 3
sh scripts/harness repair asset-flow-001 PRIVATE_DEFAULT
sh scripts/harness resume asset-flow-001
sh scripts/harness status asset-flow-001
```

실패 후 자동 수정은 `repair`로 실패한 검사 ID를 예약한 뒤 해당 복귀 단계부터 다시 시작한다.
같은 실패가 2회 수정 후에도 남으면 halted 상태를 유지한다. resume으로 횟수를 초기화하지 않는다.
SubagentStop을 받지 못해 dispatch가 남았으면 실제 작업자 종료를 먼저 확인하고 `release <ID>`를 사용한다.
release는 완료/승인/재시도 횟수를 변경하지 않는다. 상태 확인 없이 작업 중 release하지 않는다.

## 판정 범위와 한계

- 필수 파일·화면 개수·크기·지정 디자인 속성·설계 권한·상태 전이를 기계 검사한다.
- Figma 수집 실패·빈 데이터·잘못된 JSON·수집 후 변경·승인 누락은 통과하지 않는다.
- 실데이터 모드에 fixture 증거를 넣으면 차단된다. 테스트 통과는 실제 Figma 검증 완료가 아니다.
- 판정자가 의미 검토에서 역할 분류나 적용 범위 오류를 발견하면 수정 단계로 반환해야 한다.
  기계는 잘못 분류된 버튼을 자동으로 인식하지 못한다.
- 로컬 파일과 훅은 신뢰된 작업자의 실수를 막는 경계다. 관리자 권한으로 파일을 직접 고치는
  공격자까지 막는 보안 샌드박스가 아니다.
- Figma 쓰기 도구의 임의 JavaScript가 다른 영역까지 수정하지 않는지는 연결 도구 권한과
  에이전트 지침에 의존한다. 원격 영역 격리를 OS 수준으로 보장하지 않는다.
- 현재 토큰·컴포넌트 메타데이터의 형식을 검사하며, 미사용 Figma 변수/컴포넌트까지 전수 조회하지 않는다.
- 합의된 R5 기계 판정과 design.md의 전체 시각적 완성도는 별개다. 해당되지 않은 디자인 기준을
  검사했다고 주장하지 않는다.

## 테스트

```sh
python3 tests/run_validation.py
```

또는 HARNESS_PYTHON으로 지정한 Python 실행 파일을 사용한다.
가상 사례는 임시 폴더에서 실행하며 실제 runs/에 가짜 승인이나 결과를 남기지 않는다.
validation/의 보고서는 단위·통합 테스트, 실제 Figma, Claude Code 실행 상태를 각각 표시한다.

## 참고한 공식 문서

- [Claude Code 하위 에이전트](https://code.claude.com/docs/en/sub-agents)
- [Claude Code 훅](https://code.claude.com/docs/en/hooks)
- [Figma REST 파일 엔드포인트](https://developers.figma.com/docs/rest-api/file-endpoints/)
- [Figma 노드 데이터](https://developers.figma.com/docs/rest-api/file-node-types/)
