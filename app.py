
import re
import streamlit as st
from dataclasses import dataclass, field
from typing import List, Dict, Tuple

st.set_page_config(page_title="2회고사 서·논술형 자동 채점기", layout="wide")

# -----------------------------
# 공통 유틸
# -----------------------------
def norm(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text

def contains_any(text: str, words: List[str]) -> bool:
    t = norm(text)
    return any(norm(w) in t for w in words)

def contains_all_groups(text: str, groups: List[List[str]]) -> bool:
    """각 그룹에서 최소 1개 표현씩 포함하면 True"""
    t = norm(text)
    return all(any(norm(w) in t for w in group) for group in groups)

def count_groups(text: str, groups: List[List[str]]) -> int:
    t = norm(text)
    return sum(1 for group in groups if any(norm(w) in t for w in group))

def has_external_claim(text: str, banned: List[str]) -> bool:
    return contains_any(text, banned)

def strip_parenthetical_method(text: str) -> Tuple[str, str]:
    """
    문장 끝 괄호 안 방법명을 추출.
    예: '...이다. (인과)' -> ('...이다.', '인과')
    """
    m = re.search(r"\(([^()]+)\)\s*$", text.strip())
    if not m:
        return text.strip(), ""
    method = m.group(1).strip()
    body = text[:m.start()].strip()
    return body, method

# -----------------------------
# 설명 방법 판정
# -----------------------------
METHODS = ["정의", "예시", "인과", "분석", "비교와 대조", "분류와 구분"]

METHOD_ALIASES = {
    "정의": ["정의"],
    "예시": ["예시", "예"],
    "인과": ["인과", "원인과 결과"],
    "분석": ["분석"],
    "비교와 대조": ["비교와 대조", "비교·대조", "비교 대조", "대조", "비교"],
    "분류와 구분": ["분류와 구분", "분류·구분", "분류 구분", "분류", "구분"],
}

METHOD_PATTERNS = {
    "정의": [
        r".+(이란|란)\s",
        r"(말한다|뜻한다|의미한다|라고 한다|개념이다)"
    ],
    "예시": [
        r"(예를 들어|예컨대|대표적으로|예로|의 예|같은 사례|사례로)"
    ],
    "인과": [
        r"(때문에|으로 인해|로 인해|따라서|그러므로|하므로|해서|결과적으로|그 결과)"
    ],
    "분석": [
        r"(이루어져 있다|구성되어 있다|구성된다|요소|부분|구성 요소)"
    ],
    "비교와 대조": [
        r"(반면|와 달리|과 달리|둘 다|공통점|차이점|같은 점|다른 점|하지만|그러나)"
    ],
    "분류와 구분": [
        r"(에 따라.+나뉜|로 나뉜|으로 나뉜|분류|구분|묶인|묶을 수|종류)"
    ],
}

def canonical_method(name: str) -> str:
    n = norm(name)
    for canon, aliases in METHOD_ALIASES.items():
        if any(norm(a) == n for a in aliases):
            return canon
    return ""

def method_signature_matches(body: str, method: str) -> bool:
    body_n = norm(body)
    if method not in METHOD_PATTERNS:
        return False

    # 기본 표지 확인
    basic = any(re.search(p, body_n) for p in METHOD_PATTERNS[method])

    # 방법별 구조 조건 강화
    if method == "정의":
        return basic

    if method == "예시":
        return basic

    if method == "인과":
        # 원인/결과를 잇는 표지가 실제로 있어야 함
        return basic and len(body_n) >= 12

    if method == "분석":
        # 전체를 부분/요소로 나누는 언어가 있어야 함
        return basic

    if method == "비교와 대조":
        # 둘 이상의 대상이 실제로 드러나야 함
        comparison_targets = [
            ["실생활 전기", "정전기"],
            ["인간", "인공 지능"],
            ["인간의 예술", "인공 지능"],
            ["쉬운 과제", "어려운 과제"],
            ["비교적 쉬운", "도전이 필요한"],
        ]
        has_two_targets = any(all(term in body_n for term in pair) for pair in comparison_targets)
        return basic and has_two_targets

    if method == "분류와 구분":
        # 기준 + 종류 나눔이 모두 드러나야 함
        return basic and contains_any(body_n, ["따라", "기준", "종류", "나뉘", "분류", "구분"])

    return basic

# -----------------------------
# 공통 채점 결과
# -----------------------------
@dataclass
class GradeResult:
    passed: bool
    score: float | None = None
    max_score: float | None = None
    reasons: List[str] = field(default_factory=list)
    positives: List[str] = field(default_factory=list)

def result_box(title: str, r: GradeResult):
    st.markdown(f"#### {title}")
    if r.score is not None and r.max_score is not None:
        st.metric("점수", f"{r.score:g} / {r.max_score:g}")
    if r.passed:
        st.success("통과")
    else:
        st.error("재검토 필요")
    if r.positives:
        st.write("**충족 사항**")
        for x in r.positives:
            st.write(f"- {x}")
    if r.reasons:
        st.write("**보완/오답 사유**")
        for x in r.reasons:
            st.write(f"- {x}")

# -----------------------------
# 1세트: 사회적 촉진/억제
# -----------------------------
SET1_EXTERNAL = ["포모도로", "카페인", "뇌과학", "도파민", "수면", "스마트폰을 끄", "백색소음이 집중력을 높"]
SET1_WRONG = ["어려운 과제는 함께", "도전적인 과제는 함께", "사회적 촉진 때문에 혼자", "사회적 억제 때문에 함께"]

def grade_1_1(a, b, c):
    checks = []
    r = GradeResult(passed=True)
    # ㉠
    ok_a = contains_any(a, ["비교적 쉬운", "쉬운 과제", "쉬운 취미", "취미 생활", "큰 노력이 필요하지", "큰 노력을 들일 필요가 없는"])
    if ok_a: r.positives.append("㉠ 쉬운/노력이 적게 필요한 과제의 의미가 드러남")
    else: r.reasons.append("㉠에 '쉬운 과제 또는 큰 노력이 필요하지 않은 과제'의 의미가 필요함")
    # ㉡
    ok_b = contains_all_groups(b, [["혼자"], ["집중", "연습", "익숙해질 때까지", "차분"]])
    if ok_b: r.positives.append("㉡ 혼자 집중·연습하는 환경의 의미가 드러남")
    else: r.reasons.append("㉡에 최소한 '혼자'와 '집중/연습/차분함' 중 하나가 함께 드러나야 함")
    # ㉢
    ok_c = contains_any(c, ["사회적 억제"])
    if ok_c: r.positives.append("㉢ 사회적 억제를 정확히 제시함")
    else: r.reasons.append("㉢은 개념어 '사회적 억제'가 필요함")
    r.passed = ok_a and ok_b and ok_c
    return r

def grade_explanation_pair(ans1, ans2, set_no):
    r = GradeResult(passed=True)
    b1, m1_raw = strip_parenthetical_method(ans1)
    b2, m2_raw = strip_parenthetical_method(ans2)
    m1, m2 = canonical_method(m1_raw), canonical_method(m2_raw)

    if not m1:
        r.reasons.append("(1) 문장 끝에 허용된 설명 방법 명칭이 없음")
    if not m2:
        r.reasons.append("(2) 문장 끝에 허용된 설명 방법 명칭이 없음")
    if m1 and m2 and m1 == m2:
        r.reasons.append("(1)과 (2)에 같은 설명 방법을 사용함 — 서로 다른 2가지 방법 조건 위반")
    elif m1 and m2:
        r.positives.append(f"서로 다른 설명 방법 사용: {m1} / {m2}")

    if m1:
        if method_signature_matches(b1, m1):
            r.positives.append(f"(1) 실제 서술 방식과 '{m1}' 명칭이 일치함")
        else:
            r.reasons.append(f"(1) 괄호에는 '{m1}'라고 썼지만 실제 문장에 그 방법의 구조가 드러나지 않음")
    if m2:
        if method_signature_matches(b2, m2):
            r.positives.append(f"(2) 실제 서술 방식과 '{m2}' 명칭이 일치함")
        else:
            r.reasons.append(f"(2) 괄호에는 '{m2}'라고 썼지만 실제 문장에 그 방법의 구조가 드러나지 않음")

    combined = f"{b1} {b2}"

    if set_no == 1:
        source_groups = [
            ["쉬운 과제", "비교적 쉬운", "취미 생활", "큰 노력이 필요하지"],
            ["함께", "도서관", "커피숍", "모임", "혼자", "집중", "연습", "익숙해질 때까지", "도전이 필요한", "어려운 과제"]
        ]
        source_ok = count_groups(combined, source_groups) >= 2
        external = SET1_EXTERNAL
        contradictions = SET1_WRONG
        conclusion_ok = contains_any(combined, ["함께", "혼자", "집중", "연습", "도서관", "커피숍", "모임"])
    elif set_no == 2:
        source_groups = [
            ["정전기", "전하"],
            ["이동하지", "머물", "정지", "고여 있는 물", "고인 물", "전압", "위험하지", "흐르는 물", "실생활 전기"]
        ]
        source_ok = count_groups(combined, source_groups) >= 2
        external = ["습도", "건조해서", "겨울이라", "마찰 때문에", "옷을 벗", "머리카락", "금속 손잡이"]
        contradictions = ["정전기는 전하가 이동한다", "정전기는 흐르는 물", "정전기는 매우 위험", "정전기는 감전 위험이 크"]
        conclusion_ok = contains_any(combined, ["이동하지", "머물", "위험하지", "차이", "고여 있는 물", "전압이 높"])
    else:
        source_groups = [
            ["인공 지능", "ai", "인간의 예술", "인간 예술"],
            ["감정", "철학", "삶의 경험", "관점", "환경", "미술계", "예술의 범주", "상징적 가치", "이야기"]
        ]
        source_ok = count_groups(combined, source_groups) >= 2
        external = ["저작권", "학습 데이터의 불법", "창작자 일자리", "표절", "법적 책임"]
        contradictions = ["인공 지능은 감정을 느낀", "인공 지능도 삶의 경험", "인공 지능 그림은 가치가 전혀 없", "ai 그림은 가치가 전혀 없"]
        conclusion_ok = contains_any(combined, ["예술로 보기 어렵", "상징적 가치", "미술계에 큰 변화", "예술의 범주를 확장", "차이가 있", "감정이 없"])

    if source_ok:
        r.positives.append("지문 핵심 내용이 의미 수준에서 반영됨")
    else:
        r.reasons.append("지문 핵심 내용이 충분히 확인되지 않음")

    if has_external_claim(combined, external):
        r.reasons.append("지문에 제시되지 않은 외부 정보가 핵심 근거로 사용됨")
    if contains_any(combined, contradictions):
        r.reasons.append("지문의 개념 방향과 반대되는 오개념이 포함됨")
    if conclusion_ok:
        r.positives.append("문항이 요구한 결론 방향이 드러남")
    else:
        r.reasons.append("문항이 요구한 결론 방향이 명확하지 않음")

    r.passed = (
        bool(m1) and bool(m2) and m1 != m2
        and method_signature_matches(b1, m1)
        and method_signature_matches(b2, m2)
        and source_ok
        and not has_external_claim(combined, external)
        and not contains_any(combined, contradictions)
        and conclusion_ok
    )
    return r

# -----------------------------
# 2세트 1번
# -----------------------------
def grade_2_1(a, b, c):
    r = GradeResult(passed=True)
    ok_a = contains_any(a, ["높은 곳에 고여 있는 물", "고여 있는 물", "고인 물", "머물러 있는 물"])
    ok_b = contains_all_groups(b, [["전하"], ["이동하지", "움직이지", "머물", "정지"]])
    ok_c = contains_any(c, ["위험하지", "위험이 없", "감전 위험이 없", "별 피해가 없"])

    if ok_a: r.positives.append("㉠ 고여 있는 물의 비유가 드러남")
    else: r.reasons.append("㉠에 '고여 있는 물'의 의미가 필요함")
    if ok_b: r.positives.append("㉡ 전하가 이동하지 않고 머무는 상태가 드러남")
    else: r.reasons.append("㉡에 '전하 + 이동하지 않음/머무름/정지'가 필요함")
    if ok_c: r.positives.append("㉢ 위험하지 않다는 결론이 드러남")
    else: r.reasons.append("㉢에 '위험하지 않음/감전 위험 없음'의 의미가 필요함")

    wrong = contains_any(f"{a} {b} {c}", ["흐르는 물", "전하가 이동함", "매우 위험", "감전 위험이 큼"])
    if wrong:
        r.reasons.append("실생활 전기의 특성을 정전기의 특성으로 바꿔 쓴 오개념이 있음")
    r.passed = ok_a and ok_b and ok_c and not wrong
    return r

# -----------------------------
# 3세트 1번
# -----------------------------
def grade_3_1(a, b, c):
    r = GradeResult(passed=True)
    ok_a = contains_all_groups(a, [["로봇"], ["피겨", "스케이팅"], ["완벽", "실수 없이"]])
    ok_b = contains_all_groups(b, [["감정", "철학", "이야기"], ["예술로 보기 어렵", "예술이 아니", "예술로 보기 힘들"]])
    ok_c = contains_all_groups(c, [["미술계", "예술의 범주"], ["변화", "확장"], ["가치", "의미", "상징"]])

    if ok_a: r.positives.append("㉠ 로봇의 완벽한 피겨 스케이팅 비유가 드러남")
    else: r.reasons.append("㉠에 '로봇 + 피겨 스케이팅 + 완벽/실수 없음'의 의미가 필요함")
    if ok_b: r.positives.append("㉡ AI의 감정/철학/이야기 부재와 예술 판단이 연결됨")
    else: r.reasons.append("㉡에 '감정·철학·이야기 부재'와 '예술로 보기 어려움'이 함께 필요함")
    if ok_c: r.positives.append("㉢ 미술계 변화 또는 예술 범주 확장의 가치가 드러남")
    else: r.reasons.append("㉢에 '미술계 변화/예술 범주 확장'과 '가치/의미'가 함께 필요함")

    wrong = contains_any(f"{a} {b} {c}", ["인공 지능은 감정을 느낀", "가치가 전혀 없", "삶의 경험을 가진 인공 지능"])
    if wrong:
        r.reasons.append("인간 예술의 특성을 AI의 특성으로 잘못 옮긴 오개념이 있음")
    r.passed = ok_a and ok_b and ok_c and not wrong
    return r

# -----------------------------
# 영상 기획안 채점
# -----------------------------
VIDEO_RULES = {
    1: {
        "visual_groups": [
            ["혼자", "한 명", "홀로"],
            ["조용", "차분", "집중", "어려운 문제", "도전적인 과제"]
        ],
        "audio_groups": [
            ["조용", "무음", "소리를 줄", "소음 최소", "연필", "책장", "잔잔"]
        ],
        "effect_source": [
            "혼자", "차분", "집중", "어려운 과제", "도전적인 과제", "익숙해질 때까지", "연습"
        ],
        "wrong_visual": ["친구들과 함께", "여럿이 함께", "떠들며 공부"],
        "wrong_audio": ["경쾌한 음악", "시끄러운", "큰 소리", "활기찬 음악"],
        "generic_effect": ["보기 좋", "재미있", "멋있", "예쁘", "집중이 잘 된다", "이해하기 쉽"],
    },
    2: {
        "visual_groups": [
            ["고여", "고인", "멈춘", "정지", "흐르지"]
        ],
        "audio_groups": [
            ["무음", "조용", "흐르는 소리가 없", "물소리를 넣지", "정적", "소리를 줄"]
        ],
        "effect_source": [
            "전하", "이동하지", "머물", "정지", "고여 있는 물", "고인 물", "흐르지"
        ],
        "wrong_visual": ["폭포", "거세게 흐르는", "물레방아를 힘차게"],
        "wrong_audio": ["거센 물소리", "콸콸", "웅장한 물소리"],
        "generic_effect": ["보기 좋", "재미있", "멋있", "이해하기 쉽"],
    },
    3: {
        "visual_groups": [
            ["감정", "경험", "철학", "관점", "추억", "관객", "감동", "화가", "작가"]
        ],
        "audio_groups": [
            ["감정", "따뜻", "잔잔", "독백", "숨소리", "붓질", "사람 목소리", "내레이션"]
        ],
        "effect_source": [
            "감정", "철학", "삶의 경험", "관점", "환경", "감동", "울림", "인간의 예술", "작가"
        ],
        "wrong_visual": ["인공 지능이 감정을 느끼", "ai가 자신의 삶을 회상", "로봇이 감정을 담"],
        "wrong_audio": ["기계음만", "메트로놈만", "일정한 기계음"],
        "generic_effect": ["보기 좋", "재미있", "멋있", "예쁘", "그냥 감동적"],
    }
}

def grade_video(set_no, visual, visual_effect, audio, audio_effect, scored=False):
    cfg = VIDEO_RULES[set_no]
    r = GradeResult(passed=True, score=0.0 if scored else None, max_score=6.0 if scored else None)

    visual_ok = contains_all_groups(visual, cfg["visual_groups"])
    audio_ok = contains_all_groups(audio, cfg["audio_groups"])
    visual_wrong = contains_any(visual, cfg["wrong_visual"])
    audio_wrong = contains_any(audio, cfg["wrong_audio"])

    # 효과는 앞 요소와 연결 + 본문 근거가 함께 있어야 함.
    visual_link = (
        any(tok in norm(visual_effect) for tok in ["화면", "모습", "장면", "보여", "시각", "혼자", "고여", "작가", "관객", "감정"])
        and not contains_any(visual_effect, cfg["generic_effect"])
    )
    audio_link = (
        any(tok in norm(audio_effect) for tok in ["소리", "음악", "청각", "조용", "정적", "붓질", "기계음", "대비"])
        and not contains_any(audio_effect, cfg["generic_effect"])
    )
    visual_source = contains_any(visual_effect, cfg["effect_source"])
    audio_source = contains_any(audio_effect, cfg["effect_source"])

    # 1~3세트별 결론 방향
    if set_no == 1:
        visual_conclusion = contains_any(visual_effect, ["혼자", "차분", "집중", "어려운 과제"])
        audio_conclusion = contains_any(audio_effect, ["차분", "집중", "혼자", "조용"])
    elif set_no == 2:
        visual_conclusion = contains_any(visual_effect, ["전하가 이동하지", "머물", "고여", "흐르지", "정전기"])
        audio_conclusion = contains_any(audio_effect, ["전하가 이동하지", "머물", "흐르지", "정전기", "대비"])
    else:
        visual_conclusion = contains_any(visual_effect, ["인간", "작가", "감정", "경험", "관점", "감동"])
        audio_conclusion = contains_any(audio_effect, ["인간", "작가", "감정", "울림", "감동", "경험"])

    # 각 1.5점: 요소 0.75 + 해당 효과 0.75로 세분
    if scored:
        if visual_ok and not visual_wrong: r.score += 0.75
        if visual_link and visual_source and visual_conclusion: r.score += 0.75
        if audio_ok and not audio_wrong: r.score += 0.75
        if audio_link and audio_source and audio_conclusion: r.score += 0.75
        # 총 6점 문항: 효과의 "본문 근거" 비중을 추가로 각 효과 1.5점씩 반영
        # => 시각요소 0.75 + 시각효과 2.25 + 청각요소 0.75 + 청각효과 2.25 = 6
        if visual_source and visual_conclusion: r.score += 1.5
        if audio_source and audio_conclusion: r.score += 1.5

    if visual_ok and not visual_wrong:
        r.positives.append("Ⓐ 시각 요소가 지문 개념을 반영함")
    else:
        r.reasons.append("Ⓐ 시각 요소가 지문 개념을 반영하지 않거나 반대 개념이 사용됨")

    if audio_ok and not audio_wrong:
        r.positives.append("Ⓑ 청각 요소가 지문 개념을 반영함")
    else:
        r.reasons.append("Ⓑ 청각 요소가 지문 개념을 반영하지 않거나 반대 개념이 사용됨")

    if visual_link and visual_source and visual_conclusion:
        r.positives.append("Ⓐ 효과가 시각 요소와 연결되고 본문 근거 및 결론 방향을 포함함")
    else:
        r.reasons.append("Ⓐ 효과는 앞의 시각 요소와 연결되고, 본문 근거 및 요구 결론을 함께 제시해야 함")

    if audio_link and audio_source and audio_conclusion:
        r.positives.append("Ⓑ 효과가 청각 요소와 연결되고 본문 근거 및 결론 방향을 포함함")
    else:
        r.reasons.append("Ⓑ 효과는 앞의 청각 요소와 연결되고, 본문 근거 및 요구 결론을 함께 제시해야 함")

    r.passed = (
        visual_ok and audio_ok and not visual_wrong and not audio_wrong
        and visual_link and audio_link
        and visual_source and audio_source
        and visual_conclusion and audio_conclusion
    )
    return r

# -----------------------------
# UI
# -----------------------------
st.title("2회고사 대비 서·논술형 자동 채점기")
st.caption("해냄에듀 2회고사 대비 모의 문항 1~3세트 기준 · 규칙 기반 1차 자동 채점")

with st.expander("채점 원칙"):
    st.markdown("""
- **용어 자체가 없어도 의미가 맞으면 인정**합니다. 예: `전하가 움직이지 않는다` → `전하가 이동하지 않는다`와 동등하게 인정.
- 설명 방법을 괄호에 적은 경우, **명칭과 실제 문장 구조가 일치해야** 합니다.
- (1), (2)에서 **같은 설명 방법을 반복하면 조건 위반**입니다.
- 지문에 없는 외부 지식을 핵심 근거로 사용하거나, **한 개념의 특성을 다른 개념에 옮겨 쓰면 오답** 처리합니다.
- 영상 문항의 효과는 **앞서 쓴 시각/청각 요소와 연결 + 본문 근거 + 요구 결론**이 모두 있어야 통과합니다.
- 규칙 기반 채점이므로 최종 성적 확정 전 교사 확인을 권장합니다.
""")

tab1, tab2, tab3 = st.tabs(["1세트", "2세트", "3세트"])

with tab1:
    st.subheader("1세트 — 사회적 촉진·사회적 억제")
    st.markdown("### 서·논술형1")
    a = st.text_input("㉠", key="s1q1a")
    b = st.text_input("㉡", key="s1q1b")
    c = st.text_input("㉢", key="s1q1c")
    if st.button("1-1 채점", key="b11"):
        result_box("1-1 결과", grade_1_1(a,b,c))

    st.markdown("### 서·논술형2")
    st.info("모범 답안 경로 예: 예시 + 비교와 대조")
    with st.expander("모범 답안 예시"):
        st.write("(1) 예를 들어 비교적 쉬운 취미 생활이나 큰 노력을 들일 필요가 없는 과제를 할 때는 커피숍이나 도서관에서 하거나 다른 사람들과 함께 공부하는 것이 효율적일 수 있다. (예시)")
        st.write("(2) 반면 지나치게 어렵거나 도전이 필요한 과제는 쉬운 과제와 달리 충분히 연습하며 익숙해질 때까지 차분하게 혼자 집중하는 것이 좋다. (비교와 대조)")
    q21 = st.text_area("(1)", key="s1q2a")
    q22 = st.text_area("(2)", key="s1q2b")
    if st.button("1-2 채점", key="b12"):
        result_box("1-2 결과", grade_explanation_pair(q21,q22,1))

    st.markdown("### 서·논술형3")
    v = st.text_area("Ⓐ 시각 요소", key="s1v")
    ve = st.text_area("Ⓐ의 효과", key="s1ve")
    au = st.text_area("Ⓑ 청각 요소", key="s1a")
    aue = st.text_area("Ⓑ의 효과", key="s1ae")
    if st.button("1-3 채점", key="b13"):
        result_box("1-3 결과", grade_video(1,v,ve,au,aue,False))

with tab2:
    st.subheader("2세트 — 정전기")
    st.markdown("### 서·논술형1")
    a = st.text_input("㉠", key="s2q1a")
    b = st.text_input("㉡", key="s2q1b")
    c = st.text_input("㉢", key="s2q1c")
    if st.button("2-1 채점", key="b21"):
        result_box("2-1 결과", grade_2_1(a,b,c))

    st.markdown("### 서·논술형2")
    st.info("가능한 모범 경로: 정의 + 비교와 대조 / 정의 + 인과 / 비교와 대조 + 인과")
    with st.expander("선택 가능한 방법별 모범 답안"):
        st.write("정의: 정전기란 전하가 이동하지 않고 정지 상태로 머물러 있는 전기 현상을 말한다. (정의)")
        st.write("비교와 대조: 실생활 전기는 흐르는 물과 같지만 정전기는 높은 곳에 고여 있는 물과 같아 전하가 이동하지 않는다는 차이가 있다. (비교와 대조)")
        st.write("인과: 정전기는 전하가 이동하지 않고 머물러 있기 때문에 전압이 높더라도 위험하지 않다. (인과)")
    q21 = st.text_area("(1)", key="s2q2a")
    q22 = st.text_area("(2)", key="s2q2b")
    if st.button("2-2 채점", key="b22"):
        result_box("2-2 결과", grade_explanation_pair(q21,q22,2))

    st.markdown("### 서·논술형3")
    v = st.text_area("Ⓐ 시각 요소", key="s2v")
    ve = st.text_area("Ⓐ의 효과", key="s2ve")
    au = st.text_area("Ⓑ 청각 요소", key="s2a")
    aue = st.text_area("Ⓑ의 효과", key="s2ae")
    if st.button("2-3 채점", key="b23"):
        result_box("2-3 결과", grade_video(2,v,ve,au,aue,False))

with tab3:
    st.subheader("3세트 — 인공 지능 그림과 인간 예술")
    st.markdown("### 서·논술형1")
    a = st.text_input("㉠", key="s3q1a")
    b = st.text_input("㉡", key="s3q1b")
    c = st.text_input("㉢", key="s3q1c")
    if st.button("3-1 채점", key="b31"):
        result_box("3-1 결과", grade_3_1(a,b,c))

    st.markdown("### 서·논술형2")
    st.info("가능한 모범 경로: 비교와 대조 + 인과")
    with st.expander("선택 가능한 방법별 모범 답안"):
        st.write("비교와 대조: 인간의 예술에는 작가의 감정과 철학, 삶의 경험과 관점이 담기지만 인공 지능은 감정이나 독자적인 철학과 이야기가 없다는 차이가 있다. (비교와 대조)")
        st.write("인과: 인공 지능이 그린 그림은 기존 미술계에 큰 변화를 가져왔고 앞으로 예술의 범주를 확장할 수 있기 때문에 상징적인 가치를 지닌다. (인과)")
        st.write("예시(가능 경로): 대표적으로 「에드몽 드 벨라미」는 알고리즘과 데이터를 사용해 만들어져 미술계에 큰 화제를 불러온 작품이다. (예시)")
    q21 = st.text_area("(1)", key="s3q2a")
    q22 = st.text_area("(2)", key="s3q2b")
    if st.button("3-2 채점", key="b32"):
        result_box("3-2 결과", grade_explanation_pair(q21,q22,3))

    st.markdown("### 서·논술형3 [총 6점]")
    st.caption("자동 부분점수: 시각 요소 0.75 + 시각 효과 2.25 + 청각 요소 0.75 + 청각 효과 2.25")
    with st.expander("모범 답안"):
        st.write("Ⓐ 화가가 자신의 경험과 감정을 떠올리며 그림을 그리고, 관객이 완성된 작품을 보고 감동하는 모습을 보여준다.")
        st.write("Ⓐ 효과: 인간의 작품에는 작가의 감정과 삶의 경험, 관점 등이 담기며 이러한 요소가 감상자에게 감동을 준다는 점을 시각적으로 드러낸다.")
        st.write("Ⓑ 감정이 느껴지는 잔잔한 음악과 함께 붓질 소리나 사람의 숨소리를 자연스럽게 들려준다.")
        st.write("Ⓑ 효과: 장면 1의 기계적인 소리와 대비하여 인간의 예술에 담긴 감정과 경험이 감상자에게 울림을 준다는 점을 전달한다.")
    v = st.text_area("Ⓐ 시각 요소", key="s3v")
    ve = st.text_area("Ⓐ의 효과", key="s3ve")
    au = st.text_area("Ⓑ 청각 요소", key="s3a")
    aue = st.text_area("Ⓑ의 효과", key="s3ae")
    if st.button("3-3 채점", key="b33"):
        result_box("3-3 결과", grade_video(3,v,ve,au,aue,True))

st.divider()
st.caption("※ 본 앱은 교사용 1차 판정 보조 도구입니다. 규칙 기반 판정 결과는 최종 채점 전에 교사가 확인하세요.")
