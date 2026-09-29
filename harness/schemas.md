# 데이터 계약 v1

모든 JSON은 UTF-8이다. 예시는 구조 설명용이며 실제 Figma 증거나 사용자 승인으로 사용하지 않는다.
필수 필드 누락, 문자열로 쓴 boolean, 중복 화면 ID, 빈 필수 배열은 `blocked`다.
검증 구현은 scripts/core.py, 판정값의 단일 기준은 rules.json이다.

## input.json

```json
{
  "schema_version": 1,
  "run_id": "asset-flow-001",
  "flow": "내 자산 등록 → 판매 신청",
  "screen_ids": ["asset-create", "sale-request"],
  "references": ["사용자가 제공한 URL 또는 캡처 경로"],
  "scope": {
    "asset_registration": true,
    "sale_application": true,
    "not_applicable_reason": ""
  },
  "mode": "live"
}
```

scope는 시작 시 사용자 흐름 및 PRD와 대조해 정한다. false가 하나라도 있으면 해당 없음 근거가 필요하다.
`fixture`는 테스트 전용이다. 실데이터 검증으로 집계하지 않는다.

## 01-research/references.json

배열 원소 필드: `source` 문자열, `accessible` boolean, `summary` 문자열.
접근 실패 시 summary에 실패 사유를 쓴다. 전체 accessible이 false이면 실패다.

## 02-spec/screens.json

```json
{
  "screens": [{"id": "asset-create", "title": "자산 등록"}, {"id": "sale-request", "title": "판매 신청"}],
  "service": {
    "default_visibility": "private",
    "sale_roles": ["seller"],
    "states": ["private", "member_only", "sale_requested", "pending", "approved"],
    "transitions": [
      {"from":"private","to":"sale_requested","user_selected":true,"reviewed_by_operator":false},
      {"from":"pending","to":"approved","user_selected":false,"reviewed_by_operator":true}
    ]
  }
}
```

screens의 ID는 합의 목록과 정확히 일치한다. 각 전이는 실제 설계서에 근거해야 한다.
등록 기능에는 default_visibility, 등록/판매 기능에는 states와 transitions,
판매 기능에는 sale_roles와 approved로 가는 검수 전이가 필요하다.
시각 디자인만으로 접근 제어가 구현되었다고 주장하지 않는다.

## 03-concept/key-screens.json 및 04-design/screens.json

작업자가 먼저 다음 수집 manifest를 작성한다. concept_version은 3단계에서 필수다.

```json
{
  "concept_version": "v1",
  "manifest": {
    "file_key": "실제_Figma_file_key",
    "frames": [{"screen_id":"asset-create","node_id":"12:34"}],
    "roles": {"12:35":"button", "12:36":"content-card"}
  }
}
```

역할은 screen, button, badge, toggle, input, content-card, app-icon, featured-pricing,
text, layout, media, other 중 하나다. 루트는 screen, TEXT 노드는 text로 수집기가 분류한다.
그 외 **모든 하위 노드**를 roles에 명시한다. 자동으로 other를 채우지 않는다.
판정자는 버튼을 layout으로 잘못 분류하는 등 의미상의 누락이 없는지 검토한다.
app-icon의 30% 계산은 정사각형을 기준으로 한다. 비정사각형은 임의 계산 대신 판정 불가다.

`collect`는 Figma REST API에서 깊이 제한 없이 하위 노드를 읽고 같은 파일에 다음을 추가한다.

- source: kind, file_key, version, collected_at, complete, manifest_hash
- frames: screen_id, id, width, height, nodes
- nodes: id, type, role, width, height, effects, fills, corners, text_runs
- effects: type, visible
- fills: type, color(HEX 또는 null), opacity
- corners: 왼쪽 위부터 4개 숫자
- text_runs: letter_spacing, text_case (혼합 텍스트 스타일 포함)

수집 성공 시 run-state.json에 스냅샷 해시 영수증을 기록한다. 수집 실패 시 기존 영수증을 무효화한다.
live 모드는 실제 수집 영수증과 파일 해시가 일치해야 한다. 수집 후 파일/manifest를 고치면 다시 수집해야 한다.
Figma 검사는 수집 시각의 스냅샷 검사다. 원격 수정 후에는 반드시 재수집하고 재검사한다.

## 토큰과 컴포넌트

`04-design/tokens.json`: `tokens` 배열. 원소는 id, name, value, figma_variable_id.
`04-design/components.json`: `components` 배열. 원소는 id, name, figma_node_id.
빈 배열은 허용하지 않는다. 현재 기계 검사는 해당 메타데이터의 존재·형식과 수집된 화면 노드 속성을 검사한다.
Figma 변수 라이브러리 전체와 미사용 컴포넌트 전체를 별도 조회·전수 검사했다고 주장하지 않는다.

## 승인 및 상태

approval.json은 CLI approve만 기록한다. 필드:
run_id, concept_version, checkpoint(입력·규칙·컨텍스트·상위 단계·키스크린 해시), actor, message, approved_at.
사용자가 Claude Code에 직접 입력한 정확한 승인 문장을 UserPromptSubmit 훅이 기록한다.
live 모드의 approve는 이 사용자 이벤트와 현재 해시가 일치해야 성공한다.
fixture 모드의 테스트 승인에는 이 이벤트를 요구하지 않는다.

run-state.json: stages의 pending/running/blocked/complete, checkpoint, active_stage,
dispatch, halted, repairs, last_failures, collections, user_approval_intent, timestamps.
상태 파일은 오케스트레이터와 신뢰된 CLI가 관리한다. 작업자가 직접 고치지 않는다.

## 판정 결과

check-results.json: run_id, checked_at, mode, passed, checks.
checks 원소: id, status(pass/fail/blocked/not_applicable), expected, actual, evidence,
reason, return_stage, source. `passed`는 기계 검사 결과이며 실행 상태의 5단계 complete와 구분한다.
검사 단계별로 같은 ID가 여러 번 나타날 수 있으며 evidence와 return_stage로 대상을 구분한다.
