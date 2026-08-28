import hashlib
import os
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ditto_agent.llm.postfilter import filter_false_positive_time
from ditto_agent.llm.prompts import (
    BATCH_VERIFY_SYSTEM_PROMPT,
    FEW_SHOT_ALLOWLIST,
    TEXT_TRANSLATE_SYSTEM_PROMPT,
    TRANSLATE_SYSTEM_PROMPT,
    VERIFY_SYSTEM_PROMPT,
    build_batch_user_prompt,
    build_batch_verify_user_prompt,
    build_system_prompt,
    build_text_translate_user_prompt,
    build_translate_user_prompt,
    build_user_prompt,
    build_verify_user_prompt,
)
from ditto_agent.llm.retrieval import select_few_shot
from ditto_agent.schema import (
    AmbiguityCategory,
    AmbiguityItem,
    AmbiguityList,
    BatchAmbiguityList,
    BatchExtractionResult,
    CardTranslation,
    DraftContext,
    ExtractionResult,
    TextTranslation,
)


def _mock_deadline_raw(draft: str) -> str | None:
    if "내일" in draft:
        return "내일까지"
    idx = draft.find("까지")
    if idx == -1:
        return None
    tokens = draft[:idx].split()
    return " ".join(tokens[-3:]) + "까지"


def _mock_extract(draft: str, context: DraftContext) -> ExtractionResult:
    now = (
        datetime.fromisoformat(context.now_iso)
        if context.now_iso
        else datetime.now(ZoneInfo(context.sender_tz))
    )
    ambiguities: list[AmbiguityItem] = []
    deadline_raw = _mock_deadline_raw(draft)

    if "내일" in draft:
        tomorrow_18 = (now + timedelta(days=1)).replace(
            hour=18, minute=0, second=0, microsecond=0
        )
        ambiguities.append(
            AmbiguityItem(
                span="내일까지",
                category="TIME",
                reason="상대적 기한 표현이라 기준 시각이 명시되지 않음",
                candidates=[tomorrow_18.isoformat(), "custom"],
                suggestion=(
                    f"'내일까지'의 정확한 기준 시각이 필요합니다 — "
                    f"{tomorrow_18.strftime('%m/%d %H:%M')} {context.sender_tz} 기준으로 확정할까요?"
                ),
            )
        )

    if "고민" in draft or "재검토" in draft:
        ambiguities.append(
            AmbiguityItem(
                span=draft.strip(),
                category="REQUEST_INTENT",
                reason="완곡한 의견 제시가 여러 의도로 읽힐 수 있음",
                candidates=[
                    "현재 방향 유지 + 세부 보완 요청",
                    "완곡한 반대",
                    "추가 논의 요청",
                ],
                suggestion="실제 의도를 선택해주세요.",
            )
        )

    return ExtractionResult(
        task="문서 검토",
        assignee=context.receiver_name,
        deadline_raw=deadline_raw,
        request_type="검토 요청",
        decision_status="필수 반영" if ambiguities else "제안",
        expected_outcome="검토 결과와 필요한 수정사항 공유",
        ambiguities=ambiguities,
    )


# 2026-08-17 세션에서 같은 프롬프트로도 실행마다 recall/precision이 크게 흔들리는 걸
# 반복 확인(0.739→0.655→0.679→0.442 등) — 어느 정도는 extract() 자체의 샘플링
# 랜덤성 때문이라, seed를 고정해 재현성을 1차로 확보한다. temperature=0은 reasoning
# 계열 모델(gpt-5*, o1/o3/o4*)이 거부하는 경우가 있어 그 계열은 빼고 seed만 건다
# (seed는 더 폭넓게 지원됨 — gpt-4o-mini에서 직접 확인).
_SAMPLING_SEED = 42
_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_EMBEDDING_MODEL = "text-embedding-3-small"  # llm/retrieval.py의 RAG 동적 few-shot용 — chat completions RPD와 별도 풀


def _sampling_kwargs(model: str) -> dict:
    kwargs: dict = {"seed": _SAMPLING_SEED}
    if not model.startswith(_REASONING_MODEL_PREFIXES):
        kwargs["temperature"] = 0
    return kwargs


def _majority_string(values, allow_none: bool = False) -> str | None:
    # extract_consistent()의 스칼라 필드용 다수결 — None도 후보값 취급해서 "n번 중
    # m번은 아예 값이 없었다"는 것도 다수결에 반영한다(allow_none=False인 필드는
    # 스키마상 None이 안 나오므로 신경 안 써도 됨). 동률이면 Counter.most_common의
    # 첫 항목(처음 등장한 값)을 그대로 씀 — 완전한 결정성보다 "그럴듯한 대표값 하나"면
    # 충분한 용도라 과설계하지 않음.
    counted = Counter(v for v in values if allow_none or v is not None)
    if not counted:
        return None
    return counted.most_common(1)[0][0]


def _vote_extraction(
    results: list[ExtractionResult], threshold: int
) -> ExtractionResult:
    # extract_consistent()의 핵심 로직 — API 호출과 분리해둬서 테스트가 실제 LLM 응답
    # 없이 순수 함수로 다수결만 검증할 수 있게 함. 카테고리는 "n번 중 threshold번 이상
    # 등장했는지"로 채택 여부를 정하고, 대표 AmbiguityItem은 채택된 카테고리가 처음
    # 등장한 실행의 것을 그대로 씀(문구를 다시 합성하지 않음 — 실제 모델 출력을 유지).
    # seen_categories를 plain set으로 만들면 문자열 hash seed가 프로세스마다 랜덤이라
    # 순회 순서가 실행마다 바뀐다(이 버그 때문에 mock 그래프 테스트가 간헐적으로 깨졌음 —
    # interrupt가 도는 순서가 ambiguities 리스트 순서를 그대로 따르므로). dict.fromkeys()로
    # "처음 등장한 순서"를 결정적으로 보존한다.
    category_votes: Counter[AmbiguityCategory] = Counter()
    representative: dict[AmbiguityCategory, AmbiguityItem] = {}
    category_order: list[AmbiguityCategory] = []
    for result in results:
        seen_categories = dict.fromkeys(item.category for item in result.ambiguities)
        for category in seen_categories:
            category_votes[category] += 1
            if category not in representative:
                representative[category] = next(
                    item for item in result.ambiguities if item.category == category
                )
                category_order.append(category)

    winning_ambiguities = [
        representative[category]
        for category in category_order
        if category_votes[category] >= threshold
    ]

    base = results[0]
    return base.model_copy(
        update={
            "task": _majority_string(r.task for r in results),
            "assignee": _majority_string(
                (r.assignee for r in results), allow_none=True
            ),
            "deadline_raw": _majority_string(
                (r.deadline_raw for r in results), allow_none=True
            ),
            "request_type": _majority_string(r.request_type for r in results),
            "decision_status": _majority_string(r.decision_status for r in results),
            "expected_outcome": _majority_string(
                (r.expected_outcome for r in results), allow_none=True
            ),
            "ambiguities": winning_ambiguities,
        }
    )


class LLMClient:
    def __init__(self, use_rag: bool = False) -> None:
        # use_rag 기본값 False — 36케이스 전체에서 recall/precision이 크게 오르는 걸 보고
        # (1.000/0.875) 한때 True로 바꿨었으나, golden.json의 ambiguous 케이스 17개 중
        # 13개(76%)가 RAG로 **자기 자신의 원본 판단기준표 항목을 few-shot으로 그대로
        # 받아오는** 것으로 확인됨(select_few_shot()이 draft와 가장 유사한 phrase를 고르는데,
        # golden set 자체가 culture_criteria.py 항목의 패러프레이즈라 거의 항상 원본이
        # 뽑힘) — 이건 일반화가 아니라 정답 유출에 가까워서 측정치를 못 믿는다. 다시 False로
        # 되돌림(2026-08-17, docs/survey-results-analysis.md 17-4절). 리키지 없이 재검증할
        # 방법(leave-one-out 등)을 찾기 전까진 이 상태 유지.
        self.use_rag = use_rag
        # 기본값은 "mock"이 아니라 "live" — DITTO_LLM_MODE를 아예 안 정해둔 배포는 조용히
        # 가짜 응답만 내보내는 것보다 키가 없어 바로 죽는 게 훨씬 안전하다(silent failure 방지).
        # 로컬 개발용 mock은 .env.example에 명시적으로 적어둬서 그 경로는 안 바뀜.
        self.mode = os.getenv("DITTO_LLM_MODE", "live")
        if self.mode not in ("mock", "live"):
            raise ValueError(
                f"DITTO_LLM_MODE must be 'mock' or 'live', got {self.mode!r}"
            )

        self.model = os.getenv("DITTO_OPENAI_MODEL", "o3-mini")
        self._client = None
        if self.mode == "live":
            from openai import OpenAI

            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OPENAI_API_KEY가 없는데 DITTO_LLM_MODE=live(기본값)입니다 — "
                    "로컬 개발 중이면 DITTO_LLM_MODE=mock을 .env에 명시하세요."
                )
            # max_retries=0 — 기본값(2)은 429/RPD 한도 초과에도 지수 백오프로 재시도한다.
            # 하루 요청 수 자체가 막힌 상황에서 재시도는 성공 확률 없이 쿼터만 더 태우고
            # 호출 하나당 수십 초씩 조용히 늘어지게 만든다 — 빠르게 실패시키고 호출부
            # (eval/cli.py)가 그 실패를 눈에 보이게 처리하도록 한다.
            # timeout=60.0 — SDK 기본 read timeout은 600초(10분)라, 서버가 느리게 응답하거나
            # 큐잉하면 명확한 에러 없이 최대 10분간 조용히 멈춘다(2026-08-16 세션에서 실측
            # 재현됨). 60초로 줄여서 느린 호출이 빨리 실패하고 호출부가 눈에 보이게 처리하게 함.
            self._client = OpenAI(api_key=api_key, max_retries=0, timeout=60.0)

    def extract(
        self, draft: str, context: DraftContext, few_shot_ids: set[str] | None = None
    ) -> ExtractionResult:
        # few_shot_ids를 명시적으로 넘기면(주로 eval/cli.py가 캐시 키와 실제 호출에 같은 값을
        # 쓰려고) 그대로 쓰고, 안 넘겼는데 self.use_rag면 그때 select_few_shot()으로 직접
        # 고른다 — 임베딩 API를 중복 호출하지 않으려고 "누가 먼저 계산했는지"를 구분함.
        if self.mode == "mock":
            result = _mock_extract(draft, context)
            return result.model_copy(
                update={
                    "ambiguities": filter_false_positive_time(draft, result.ambiguities)
                }
            )

        if few_shot_ids is None and self.use_rag:
            few_shot_ids = select_few_shot(
                self.embed, draft, k=6, fallback=FEW_SHOT_ALLOWLIST
            )

        now_iso = (
            context.now_iso or datetime.now(ZoneInfo(context.sender_tz)).isoformat()
        )
        completion = self._client.chat.completions.parse(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": build_system_prompt(few_shot_ids=few_shot_ids),
                },
                {
                    "role": "user",
                    "content": build_user_prompt(
                        draft,
                        context.sender_tz,
                        context.receiver_tz,
                        now_iso,
                        context.recent_messages,
                        context.attachment_contexts,
                    ),
                },
            ],
            response_format=ExtractionResult,
            **_sampling_kwargs(self.model),
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError(
                "OpenAI가 구조화된 응답을 반환하지 않음 (refusal 등) — completion 로그 확인 필요"
            )
        return parsed.model_copy(
            update={
                "ambiguities": filter_false_positive_time(draft, parsed.ambiguities)
            }
        )

    def verify(
        self, draft: str, ambiguities: list[AmbiguityItem]
    ) -> list[AmbiguityItem]:
        # 1차 extract()가 flag한 후보를 회의적으로 재검토해 과탐지를 제거하는 2차 호출.
        # mock 모드는 필터링 없이 그대로 통과 — 회귀 테스트에서 그래프 배선만 확인하면 되고,
        # mock 추출기 자체가 이미 최소한의 후보만 내므로 걸러낼 게 없음.
        if self.mode == "mock" or not ambiguities:
            return ambiguities

        completion = self._client.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_verify_user_prompt(
                        draft, [a.model_dump() for a in ambiguities]
                    ),
                },
            ],
            response_format=AmbiguityList,
            **_sampling_kwargs(self.model),
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError(
                "OpenAI가 구조화된 응답을 반환하지 않음 (refusal 등) — completion 로그 확인 필요"
            )
        return parsed.ambiguities

    def verify_batch(
        self, items: list[tuple[str, list[AmbiguityItem]]]
    ) -> dict[int, list[AmbiguityItem]]:
        # verify()의 배치판 — extract_batch()와 같은 이유(RPD 절약)로 eval 전용.
        # 항목이 아예 없는(ambiguities=[]) 케이스는 호출할 필요가 없어 결과에 미리 채워둠.
        if self.mode == "mock":
            return {i: ambiguities for i, (_, ambiguities) in enumerate(items)}

        results: dict[int, list[AmbiguityItem]] = {}
        entries = []
        for i, (draft, ambiguities) in enumerate(items):
            if not ambiguities:
                results[i] = []
                continue
            entries.append((i, draft, [a.model_dump() for a in ambiguities]))

        if not entries:
            return results

        completion = self._client.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": BATCH_VERIFY_SYSTEM_PROMPT},
                {"role": "user", "content": build_batch_verify_user_prompt(entries)},
            ],
            response_format=BatchAmbiguityList,
            **_sampling_kwargs(self.model),
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError(
                "OpenAI가 구조화된 응답을 반환하지 않음 (refusal 등) — completion 로그 확인 필요"
            )
        results.update({item.index: item.ambiguities for item in parsed.items})
        return results

    def extract_batch(
        self,
        items: list[tuple[str, DraftContext]],
        few_shot_ids: set[str] | None = None,
    ) -> dict[int, ExtractionResult]:
        # 골든셋 평가처럼 서로 무관한 메시지 다수를 한 번에 처리할 때 씀 — 요청 수(RPD) 자체가
        # 쿼터인 계정에서는 메시지당 호출 1개보다 이게 훨씬 아낀다. 실사용 흐름(interface.start())은
        # 항상 메시지 1개라 이 메서드를 안 씀 — 배치는 eval 전용.
        # few_shot_ids: 배치 안 항목들이 서로 다른 draft면(일반 eval batch) 하나의 공유
        # system prompt에 draft별 few-shot을 못 반영하지만, extract_consistent()처럼 배치 전체가
        # "같은 draft를 n번 복제"한 경우엔 few_shot_ids 하나만 계산해서 그대로 넘기면 된다 —
        # 호출부가 그 특수 케이스인지 판단해서 넘겨줌(이 메서드는 그냥 있으면 쓰고 없으면
        # 기본 allowlist).
        drafts_by_index = {i: draft for i, (draft, _) in enumerate(items)}

        if self.mode == "mock":
            mock_results = {}
            for i, (draft, ctx) in enumerate(items):
                r = _mock_extract(draft, ctx)
                mock_results[i] = r.model_copy(
                    update={
                        "ambiguities": filter_false_positive_time(draft, r.ambiguities)
                    }
                )
            return mock_results

        entries = []
        for i, (draft, ctx) in enumerate(items):
            now_iso = ctx.now_iso or datetime.now(ZoneInfo(ctx.sender_tz)).isoformat()
            entries.append((i, draft, ctx.sender_tz, ctx.receiver_tz, now_iso))

        completion = self._client.chat.completions.parse(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": build_system_prompt(
                        batch=True, few_shot_ids=few_shot_ids
                    ),
                },
                {"role": "user", "content": build_batch_user_prompt(entries)},
            ],
            response_format=BatchExtractionResult,
            **_sampling_kwargs(self.model),
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError(
                "OpenAI가 구조화된 응답을 반환하지 않음 (refusal 등) — completion 로그 확인 필요"
            )
        return {
            item.index: item.extraction.model_copy(
                update={
                    "ambiguities": filter_false_positive_time(
                        drafts_by_index[item.index], item.extraction.ambiguities
                    )
                }
            )
            for item in parsed.items
        }

    def extract_consistent(
        self,
        draft: str,
        context: DraftContext,
        n: int = 3,
        threshold: int | None = None,
        few_shot_ids: set[str] | None = None,
    ) -> ExtractionResult:
        # seed 고정만으로는 배치 구조화 출력의 실행 간 노이즈가 안 사라진다는 걸 실측으로
        # 확인함(survey-results-analysis.md 13절) — 같은 메시지를 n번 독립 추출해 카테고리
        # 단위로 다수결하면 이 노이즈를 구조적으로 상쇄할 수 있다. extract_batch()를 재사용해
        # 같은 (draft, context)를 n개 항목처럼 묶어 보내므로 API 호출 수는 그대로 1번(배치
        # 크기만 n)이라 RPD 부담이 늘지 않는다.
        # threshold 기본값은 과반(n//2+1)이 아니라 **만장일치(n)** — 2026-08-17 실측(13-1절)에서
        # 과반은 오히려 precision을 깎아먹었고(0.714) 만장일치로 올리니 recall/precision 둘 다
        # 만점이 나왔다. 더 관대한 기준을 원하면 호출부에서 threshold를 명시적으로 낮추면 됨.
        # few_shot_ids: extract()와 같은 패턴 — 명시적으로 넘기면 그대로 쓰고, 안 넘겼는데
        # self.use_rag면 여기서 한 번만 계산(n개 항목이 전부 같은 draft라 한 번으로 충분).
        threshold = threshold if threshold is not None else n
        if few_shot_ids is None and self.use_rag:
            few_shot_ids = select_few_shot(
                self.embed, draft, k=6, fallback=FEW_SHOT_ALLOWLIST
            )
        runs = self.extract_batch([(draft, context)] * n, few_shot_ids=few_shot_ids)
        results = [runs[i] for i in range(n)]
        return _vote_extraction(results, threshold)

    def embed(self, text: str) -> list[float]:
        # llm/retrieval.py의 RAG 동적 few-shot 선택에 씀 — mock 모드는 실제 벡터 대신 텍스트
        # 해시로 만든 가짜 벡터를 반환한다(같은 입력엔 항상 같은 값이라 코사인 유사도 로직
        # 자체는 mock으로도 테스트 가능, 실제 의미 유사도는 아님).
        if self.mode == "mock":
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            return [b / 255 for b in digest]

        response = self._client.embeddings.create(model=_EMBEDDING_MODEL, input=text)
        return response.data[0].embedding

    def translate_card_fields(
        self,
        task: str,
        request_type: str,
        expected_outcome: str | None,
        interpretation_note: str | None,
        notes: list[str],
        target_lang: str,
    ) -> CardTranslation:
        # 확정된 카드의 자유 텍스트만 옮긴다 — deadline/decision_status/timestamp 등 구조화된
        # 필드는 그대로 둔다(숫자·고정 어휘는 번역 대상이 아니라 프론트 로컬라이즈 대상).
        if self.mode == "mock":
            prefix = f"[{target_lang}] "
            return CardTranslation(
                task=prefix + task,
                request_type=prefix + request_type,
                expected_outcome=(prefix + expected_outcome)
                if expected_outcome
                else None,
                interpretation_note=(prefix + interpretation_note)
                if interpretation_note
                else None,
                notes=[prefix + n for n in notes],
            )

        completion = self._client.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_translate_user_prompt(
                        task,
                        request_type,
                        expected_outcome,
                        interpretation_note,
                        notes,
                        target_lang,
                    ),
                },
            ],
            response_format=CardTranslation,
            **_sampling_kwargs(self.model),
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError(
                "OpenAI가 구조화된 응답을 반환하지 않음 (refusal 등) — completion 로그 확인 필요"
            )
        return parsed

    def translate_text(
        self,
        content: str,
        source_lang: str,
        target_lang: str,
    ) -> TextTranslation:
        if self.mode == "mock":
            return TextTranslation(
                translated_content=f"[{target_lang}] {content}",
            )

        translation_model = os.getenv("DITTO_TRANSLATION_MODEL", "gpt-4o-mini")
        completion = self._client.chat.completions.parse(
            model=translation_model,
            messages=[
                {"role": "system", "content": TEXT_TRANSLATE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_text_translate_user_prompt(
                        content, source_lang, target_lang
                    ),
                },
            ],
            response_format=TextTranslation,
            **_sampling_kwargs(translation_model),
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError(
                "OpenAI가 구조화된 번역 응답을 반환하지 않음 (refusal 등)"
            )
        return parsed
