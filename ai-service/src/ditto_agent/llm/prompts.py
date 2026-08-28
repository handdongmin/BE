from ditto_agent.llm.culture_criteria import as_few_shot_examples
from ditto_agent.schema import DECISION_STATUS_VOCABULARY

SYSTEM_PRINCIPLE = """당신은 비동기 업무 메시지에서 시간·요청 의도·결정 상태의 모호성을 발견해,
발신자가 스스로 명시적으로 확정하도록 돕는 비서입니다.

- 특정 국가/문화권 사람이 이 표현을 어떻게 받아들일지 당신이 임의로 단정하지 않습니다.
- 모호성이 없다면 억지로 만들어내지 마세요 — 불필요한 경고는 사용자를 방해합니다.
- 시간 표현은 임의로 확정하지 말고, 확인이 필요한 후보 시각을 제시하세요.
- 의미가 여러 갈래로 읽히는 표현은 실제 의도를 발신자가 고르도록 후보 해석을 제시하세요."""

# docs/문화_판단기준표_초안.md 20개 중 OTHER(C01-04)는 제외(docs/research-other-category.md
# 참고) + 카테고리당 대표 예시 2개씩만 선별. "few-shot이 많을수록 좋은 게 아니라 5~7개
# 이후 정체되고, 과하면 모델이 산만해져 오히려 정확도가 떨어질 수 있다"는 문헌 근거로
# 21개(양성 16 + 부정 5)에서 11개로 줄임 — 토큰도 같이 줄어 TPM 여유도 생김
# (docs/research-few-shot-efficiency.md).
#
# 2026-08-16 설문(docs/survey-results-analysis.md)으로 T03/F04/D01/D03이 "국내수렴형"
# (국내 화자는 이미 한 해석으로 강하게 쏠림)이라는 게 확인돼, T04/F02/D02/D04(설문에서
# 실제로 표준편차가 가장 컸던 = 국내에서도 진짜 갈리는 항목)로 교체 — 모호성 탐지
# few-shot 신호를 더 선명하게 하려는 의도.
FEW_SHOT_ALLOWLIST = {"T01", "T04", "F01", "F02", "D02", "D04"}
FEW_SHOT_EXAMPLES: list[dict] = as_few_shot_examples(ids=FEW_SHOT_ALLOWLIST)

# culture_criteria.py 20개가 전부 "모호함" 양성 예시뿐이라, 골든셋 평가에서 명시적 문장까지
# 전부 오탐(precision 0.51)하는 걸 확인함 — 부정 예시가 하나도 없어 "항상 뭔가는 모호하다"는
# 패턴을 학습한 것으로 보임. golden.json과 겹치지 않는 새 문장으로 대조 예시를 넣는다.
NEGATIVE_FEW_SHOT_EXAMPLES: list[str] = [
    "9월 2일 오전 10시(KST)까지 회신 부탁드립니다.",
    "이대로 최종 승인합니다. 추가 수정 없이 그대로 진행해주세요.",
    "정식 승인 완료했습니다. 바로 착수하셔도 됩니다.",
    "예산은 200만원으로 확정했고, 지난 회의에서 합의된 대로 진행합니다.",
    "오늘 회의는 예정대로 3시에 진행됩니다.",
]

OUTPUT_SCHEMA_NOTE = f"""다음 필드를 가진 JSON으로만 응답하세요:
task, assignee(nullable), deadline_raw(nullable), request_type, decision_status,
expected_outcome(nullable),
ambiguities(list of {{span, category(TIME|REQUEST_INTENT|DECISION_STATUS), reason,
candidates, suggestion}}).

- category가 TIME인 항목의 candidates는 반드시 ISO8601 절대시각 문자열이어야 합니다
  (예: "2026-08-16T18:00:00+09:00"). 설명 문장을 넣지 마세요 — 프론트가 이 값을 그대로
  파싱해서 화면에 포맷하고, 수신자 시간대 변환·근무시간 충돌 검사에도 그대로 씁니다.
  직접 입력을 허용하려면 candidates에 문자열 "custom"을 추가하세요.
- 같은 원문 구간(span)에서 나온 모호성이 시간 관련이면 TIME 항목 하나로 합치세요 —
  "정확한 시각"과 "필수 여부"처럼 관련된 질문을 별도 ambiguity 항목으로 쪼개지 마세요.
- decision_status는 발신자/수신자가 서로 다른 조직(회사·팀) 소속이라 "완료"/"승인"/
  "컨펌" 같은 말의 뜻이 다를 수 있다는 전제로, 다음 중 하나로 **정규화**해서 쓰세요
  (원문 그대로 옮기지 말 것): {", ".join(DECISION_STATUS_VOCABULARY)}. 모호하면
  DECISION_STATUS 카테고리로 ambiguities에 추가해 확인받으세요."""


def build_system_prompt(
    batch: bool = False, few_shot_ids: set[str] | None = None
) -> str:
    # few_shot_ids가 주어지면(RAG 동적 선택, llm/retrieval.py 참고) 고정 allowlist 대신 그
    # id들로 few-shot을 구성한다 — 생략하면 지금까지처럼 고정 6개(FEW_SHOT_ALLOWLIST)를 씀.
    examples = (
        as_few_shot_examples(ids=few_shot_ids)
        if few_shot_ids is not None
        else FEW_SHOT_EXAMPLES
    )
    positive = "\n\n".join(
        f"예시 입력: {ex['input']}\n예시 모호성: {ex['ambiguity']}" for ex in examples
    )
    negative = "\n\n".join(
        f"예시 입력: {text}\n예시 결과: ambiguities: [] (모호성 없음 — 경고를 만들어내지 않음)"
        for text in NEGATIVE_FEW_SHOT_EXAMPLES
    )
    schema_note = (
        f"{OUTPUT_SCHEMA_NOTE}\n\n{BATCH_OUTPUT_SCHEMA_NOTE}"
        if batch
        else OUTPUT_SCHEMA_NOTE
    )
    return (
        f"{SYSTEM_PRINCIPLE}\n\n{schema_note}\n\n"
        f"[few-shot 예시 — 모호성 있음]\n{positive}\n\n"
        f"[few-shot 예시 — 모호성 없음, ambiguities는 반드시 빈 리스트]\n{negative}"
    )


def build_user_prompt(
    draft: str,
    sender_tz: str,
    receiver_tz: str,
    now_iso: str,
    recent_messages: list[str] | None = None,
    attachment_contexts: list[str] | None = None,
) -> str:
    return (
        f"발신자 시간대: {sender_tz} (현재 {now_iso})\n"
        f"수신자 시간대: {receiver_tz}\n"
        f"최근 대화(참고 문맥일 뿐, 초안보다 우선하지 않음): {recent_messages or []}\n"
        f"첨부파일 추출 문맥(근거가 없으면 추측하지 않음): {attachment_contexts or []}\n"
        f"메시지 초안:\n{draft}"
    )


BATCH_OUTPUT_SCHEMA_NOTE = """여러 개의 서로 무관한 메시지가 [index] 태그로 주어집니다.
각 메시지를 독립적으로 처리하고(서로 참조하지 마세요), 응답은
{items: [{index, extraction: {task, assignee, deadline_raw, request_type, decision_status, expected_outcome,
ambiguities}}]} 형태로 주어진 메시지 개수만큼 정확히 하나씩 포함하세요. 순서는 상관없지만
index는 절대 빠뜨리거나 중복하지 마세요."""


def build_batch_user_prompt(entries: list[tuple[int, str, str, str, str]]) -> str:
    # entries: (index, draft, sender_tz, receiver_tz, now_iso)
    blocks = [
        f"[{i}] 발신자 시간대: {sender_tz} (현재 {now_iso}) / 수신자 시간대: {receiver_tz}\n메시지: {draft}"
        for i, draft, sender_tz, receiver_tz, now_iso in entries
    ]
    return "\n\n".join(blocks)


# 2026-08-16 reason-sync 실험(docs/survey-results-analysis.md 8절)으로 recall은 올랐지만
# (0.810→0.905) precision이 떨어짐(0.739→0.655) — REQUEST_INTENT/DECISION_STATUS에서
# 명시적 문장까지 과탐지. 1차 추출 뒤에 회의적으로 재검토하는 2차 호출을 추가해 과탐지만
# 골라 제거한다(새 항목 추가는 금지 — 1차 결과의 부분집합만 반환하게 해서 recall 손실 방지).
VERIFY_SYSTEM_PROMPT = """당신은 1차 추출이 flag한 "모호성 후보" 목록을 회의적으로 재검토하는
검수자입니다.

- 원문을 다시 읽고, 각 후보가 **진짜로 여러 해석이 가능한지** 판단하세요.
- 이미 문장 안에 명시적 조건(구체적 날짜/시각, "최종", "필수", "그대로 진행" 같은 확정 표현)이
  있어서 실제로는 헷갈릴 여지가 없다면 그 후보는 제거하세요.
- 정말 모호한 후보만 남기세요 — span/category/reason/candidates/suggestion 내용은 그대로
  유지합니다(다시 쓰지 마세요).
- **새 후보를 추가하지 마세요** — 1차 목록의 부분집합만 반환합니다. 전부 진짜 모호하면 그대로
  전부 반환하고, 전부 아니면 빈 리스트를 반환하세요."""


def build_verify_user_prompt(draft: str, ambiguities: list[dict]) -> str:
    items = "\n".join(
        f"- span: {a['span']!r} / category: {a['category']} / reason: {a['reason']}"
        for a in ambiguities
    )
    return f"원문 메시지:\n{draft}\n\n1차 추출이 flag한 모호성 후보:\n{items}"


BATCH_VERIFY_SYSTEM_PROMPT = f"""{VERIFY_SYSTEM_PROMPT}

여러 개의 서로 무관한 메시지가 [index] 태그로 주어집니다. 각 메시지를 독립적으로
재검토하고(서로 참조하지 마세요), 응답은 {{items: [{{index, ambiguities}}]}} 형태로
주어진 메시지 개수만큼 정확히 하나씩 포함하세요. 순서는 상관없지만 index는 절대
빠뜨리거나 중복하지 마세요."""


def build_batch_verify_user_prompt(entries: list[tuple[int, str, list[dict]]]) -> str:
    # entries: (index, draft, ambiguities)
    blocks = []
    for i, draft, ambiguities in entries:
        items = "\n".join(
            f"  - span: {a['span']!r} / category: {a['category']} / reason: {a['reason']}"
            for a in ambiguities
        )
        blocks.append(
            f"[{i}] 원문 메시지: {draft}\n1차 추출이 flag한 모호성 후보:\n{items}"
        )
    return "\n\n".join(blocks)


TRANSLATE_SYSTEM_PROMPT = """이미 발신자가 모호성 확인까지 끝낸, 확정된 업무 조건 필드를
번역합니다. 뜻을 바꾸거나 새로 해석하지 말고 있는 그대로 옮기세요 — 모호성 해석은 이미
끝났으므로 여기서 다른 뜻으로 번역하면 안 됩니다. task/request_type/expected_outcome/interpretation_note/
notes 필드만 응답하세요(다른 필드 없음)."""


TEXT_TRANSLATE_SYSTEM_PROMPT = """업무용 채팅 메시지를 지정된 언어로 정확하게 번역합니다.
원문의 의미, 어조, 고유명사, 숫자, 날짜와 링크를 보존하고 내용을 추가하거나 생략하지 마세요.
translated_content 필드만 응답하세요."""


def build_text_translate_user_prompt(
    content: str,
    source_lang: str,
    target_lang: str,
) -> str:
    return (
        f"원문 언어 코드: {source_lang}\n"
        f"대상 언어 코드: {target_lang}\n"
        f"번역할 메시지:\n{content}"
    )


def build_translate_user_prompt(
    task: str,
    request_type: str,
    expected_outcome: str | None,
    interpretation_note: str | None,
    notes: list[str],
    target_lang: str,
) -> str:
    return (
        f"다음 필드들을 언어 코드 '{target_lang}'로 번역하세요:\n"
        f"task: {task}\n"
        f"request_type: {request_type}\n"
        f"expected_outcome: {expected_outcome}\n"
        f"interpretation_note: {interpretation_note}\n"
        f"notes: {notes}"
    )
