
import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Tuple

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(
    page_title="2회고사 서·논술형 자동 채점기",
    page_icon="📝",
    layout="wide",
)

# =========================================================
# 1. 공통 유틸
# =========================================================

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
    return sum(
        1 for group in groups
        if any(norm(w) in t for w in group)
    )


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


# =========================================================
# 2. 설명 방법 판정
# =========================================================

METHODS = [
    "정의",
    "예시",
    "인과",
    "분석",
    "비교와 대조",
    "분류와 구분",
]

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
        r"(말한다|뜻한다|의미한다|라고 한다|개념이다)",
    ],
    "예시": [
        r"(예를 들어|예컨대|대표적으로|예로|의 예|같은 사례|사례로)",
    ],
    "인과": [
        r"(때문에|으로 인해|로 인해|따라서|그러므로|하므로|해서|결과적으로|그 결과)",
    ],
    "분석": [
        r"(이루어져 있다|구성되어 있다|구성된다|요소|부분|구성 요소)",
    ],
    "비교와 대조": [
        r"(반면|와 달리|과 달리|둘 다|공통점|차이점|같은 점|다른 점|하지만|그러나)",
    ],
    "분류와 구분": [
        r"(에 따라.+나뉜|로 나뉜|으로 나뉜|분류|구분|묶인|묶을 수|종류)",
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

    basic = any(re.search(p, body_n) for p in METHOD_PATTERNS[method])

    if method in ["정의", "예시", "인과", "분석"]:
        return basic

    if method == "비교와 대조":
        target_pairs = [
            ["실생활 전기", "정전기"],
            ["인간", "인공 지능"],
            ["인간의 예술", "인공 지능"],
            ["쉬운 과제", "어려운 과제"],
            ["비교적 쉬운", "도전이 필요한"],
        ]
        has_two_targets = any(
            all(term in body_n for term in pair)
            for pair in target_pairs
        )
        return basic and has_two_targets

    if method == "분류와 구분":
        return basic and contains_any(
            body_n,
            ["따라", "기준", "종류", "나뉘", "분류", "구분"],
        )

    return basic


# =========================================================
# 3. 채점 결과 구조
# =========================================================

@dataclass
class GradeResult:
    passed: bool
    score: float | None = None
    max_score: float | None = None
    reasons: List[str] = field(default_factory=list)
    positives: List[str] = field(default_factory=list)

    @property
    def status(self):
        return "통과" if self.passed else "재검토"


def result_box(title: str, r: GradeResult):
    st.markdown(f"#### {title}")

    if r.score is not None and r.max_score is not None:
        st.metric("점수", f"{r.score:g} / {r.max_score:g}")

    if r.passed:
        st.success("통과")
    else:
        st.warning("재검토 필요")

    if r.positives:
        st.write("**충족 사항**")
        for x in r.positives:
            st.write(f"- {x}")

    if r.reasons:
        st.write("**보완/오답 사유**")
        for x in r.reasons:
            st.write(f"- {x}")


# =========================================================
# 4. 1세트 채점
# =========================================================

SET1_EXTERNAL = [
    "포모도로",
    "카페인",
    "뇌과학",
    "도파민",
    "수면",
    "스마트폰을 끄",
    "백색소음이 집중력을 높",
]

SET1_WRONG = [
    "어려운 과제는 함께",
    "도전적인 과제는 함께",
    "사회적 촉진 때문에 혼자",
    "사회적 억제 때문에 함께",
]


def grade_1_1(a, b, c):
    r = GradeResult(passed=True)

    ok_a = contains_any(
        a,
        [
            "비교적 쉬운",
            "쉬운 과제",
            "쉬운 취미",
            "취미 생활",
            "큰 노력이 필요하지",
            "큰 노력을 들일 필요가 없는",
        ],
    )

    ok_b = contains_all_groups(
        b,
        [
            ["혼자"],
            ["집중", "연습", "익숙해질 때까지", "차분"],
        ],
    )

    ok_c = contains_any(c, ["사회적 억제"])

    if ok_a:
        r.positives.append("㉠ 쉬운/노력이 적게 필요한 과제의 의미가 드러남")
    else:
        r.reasons.append("㉠에 '쉬운 과제 또는 큰 노력이 필요하지 않은 과제'의 의미가 필요함")

    if ok_b:
        r.positives.append("㉡ 혼자 집중·연습하는 환경의 의미가 드러남")
    else:
        r.reasons.append("㉡에 최소한 '혼자'와 '집중/연습/차분함' 중 하나가 함께 드러나야 함")

    if ok_c:
        r.positives.append("㉢ 사회적 억제를 정확히 제시함")
    else:
        r.reasons.append("㉢은 개념어 '사회적 억제'가 필요함")

    r.passed = ok_a and ok_b and ok_c
    return r


# =========================================================
# 5. 설명 방법 문항 공통 채점
# =========================================================

def grade_explanation_pair(ans1, ans2, set_no):
    r = GradeResult(passed=True)

    b1, m1_raw = strip_parenthetical_method(ans1)
    b2, m2_raw = strip_parenthetical_method(ans2)

    m1 = canonical_method(m1_raw)
    m2 = canonical_method(m2_raw)

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
            r.reasons.append(f"(1) 괄호에는 '{m1}'라고 썼지만 실제 문장에 그 방법의 특성이 드러나지 않음")

    if m2:
        if method_signature_matches(b2, m2):
            r.positives.append(f"(2) 실제 서술 방식과 '{m2}' 명칭이 일치함")
        else:
            r.reasons.append(f"(2) 괄호에는 '{m2}'라고 썼지만 실제 문장에 그 방법의 특성이 드러나지 않음")

    combined = f"{b1} {b2}"

    if set_no == 1:
        source_groups = [
            ["쉬운 과제", "비교적 쉬운", "취미 생활", "큰 노력이 필요하지"],
            ["함께", "도서관", "커피숍", "모임", "혼자", "집중", "연습", "익숙해질 때까지", "도전이 필요한", "어려운 과제"],
        ]
        external = SET1_EXTERNAL
        contradictions = SET1_WRONG
        conclusion_ok = contains_any(
            combined,
            ["함께", "혼자", "집중", "연습", "도서관", "커피숍", "모임"],
        )

    elif set_no == 2:
        source_groups = [
            ["정전기", "전하"],
            ["이동하지", "머물", "정지", "고여 있는 물", "고인 물", "전압", "위험하지", "흐르는 물", "실생활 전기"],
        ]
        external = [
            "습도",
            "건조해서",
            "겨울이라",
            "마찰 때문에",
            "옷을 벗",
            "머리카락",
            "금속 손잡이",
        ]
        contradictions = [
            "정전기는 전하가 이동한다",
            "정전기는 흐르는 물",
            "정전기는 매우 위험",
            "정전기는 감전 위험이 크",
        ]
        conclusion_ok = contains_any(
            combined,
            ["이동하지", "머물", "위험하지", "차이", "고여 있는 물", "전압이 높"],
        )

    else:
        source_groups = [
            ["인공 지능", "ai", "인간의 예술", "인간 예술"],
            ["감정", "철학", "삶의 경험", "관점", "환경", "미술계", "예술의 범주", "상징적 가치", "이야기"],
        ]
        external = [
            "저작권",
            "학습 데이터의 불법",
            "창작자 일자리",
            "표절",
            "법적 책임",
        ]
        contradictions = [
            "인공 지능은 감정을 느낀",
            "인공 지능도 삶의 경험",
            "인공 지능 그림은 가치가 전혀 없",
            "ai 그림은 가치가 전혀 없",
        ]
        conclusion_ok = contains_any(
            combined,
            ["예술로 보기 어렵", "상징적 가치", "미술계에 큰 변화", "예술의 범주를 확장", "차이가 있", "감정이 없"],
        )

    source_ok = count_groups(combined, source_groups) >= 2
    has_external = contains_any(combined, external)
    has_contradiction = contains_any(combined, contradictions)

    if source_ok:
        r.positives.append("지문 핵심 내용이 의미 수준에서 반영됨")
    else:
        r.reasons.append("지문 핵심 내용이 충분히 확인되지 않음")

    if has_external:
        r.reasons.append("지문에 제시되지 않은 외부 정보가 핵심 근거로 사용됨")

    if has_contradiction:
        r.reasons.append("지문의 개념 방향과 반대되는 오개념이 포함됨")

    if conclusion_ok:
        r.positives.append("문항이 요구한 결론 방향이 드러남")
    else:
        r.reasons.append("문항이 요구한 결론 방향이 명확하지 않음")

    r.passed = (
        bool(m1)
        and bool(m2)
        and m1 != m2
        and method_signature_matches(b1, m1)
        and method_signature_matches(b2, m2)
        and source_ok
        and not has_external
        and not has_contradiction
        and conclusion_ok
    )

    return r


# =========================================================
# 6. 2세트 표 문항
# =========================================================

def grade_2_1(a, b, c):
    r = GradeResult(passed=True)

    ok_a = contains_any(
        a,
        ["높은 곳에 고여 있는 물", "고여 있는 물", "고인 물", "머물러 있는 물"],
    )

    ok_b = contains_all_groups(
        b,
        [
            ["전하"],
            ["이동하지", "움직이지", "머물", "정지"],
        ],
    )

    ok_c = contains_any(
        c,
        ["위험하지", "위험이 없", "감전 위험이 없", "별 피해가 없"],
    )

    wrong = contains_any(
        f"{a} {b} {c}",
        ["전하가 이동함", "매우 위험", "감전 위험이 큼"],
    )

    if ok_a:
        r.positives.append("㉠ 고여 있는 물의 비유가 드러남")
    else:
        r.reasons.append("㉠에 '고여 있는 물'의 의미가 필요함")

    if ok_b:
        r.positives.append("㉡ 전하가 이동하지 않고 머무는 상태가 드러남")
    else:
        r.reasons.append("㉡에 '전하 + 이동하지 않음/머무름/정지'가 필요함")

    if ok_c:
        r.positives.append("㉢ 위험하지 않다는 결론이 드러남")
    else:
        r.reasons.append("㉢에 '위험하지 않음/감전 위험 없음'의 의미가 필요함")

    if wrong:
        r.reasons.append("실생활 전기의 특성을 정전기의 특성으로 바꿔 쓴 오개념이 있음")

    r.passed = ok_a and ok_b and ok_c and not wrong
    return r


# =========================================================
# 7. 3세트 표 문항
# =========================================================

def grade_3_1(a, b, c):
    r = GradeResult(passed=True)

    ok_a = contains_all_groups(
        a,
        [
            ["로봇"],
            ["피겨", "스케이팅"],
            ["완벽", "실수 없이"],
        ],
    )

    ok_b = contains_all_groups(
        b,
        [
            ["감정", "철학", "이야기"],
            ["예술로 보기 어렵", "예술이 아니", "예술로 보기 힘들"],
        ],
    )

    ok_c = contains_all_groups(
        c,
        [
            ["미술계", "예술의 범주"],
            ["변화", "확장"],
            ["가치", "의미", "상징"],
        ],
    )

    wrong = contains_any(
        f"{a} {b} {c}",
        [
            "인공 지능은 감정을 느낀",
            "가치가 전혀 없",
            "삶의 경험을 가진 인공 지능",
        ],
    )

    if ok_a:
        r.positives.append("㉠ 로봇의 완벽한 피겨 스케이팅 비유가 드러남")
    else:
        r.reasons.append("㉠에 '로봇 + 피겨 스케이팅 + 완벽/실수 없음'의 의미가 필요함")

    if ok_b:
        r.positives.append("㉡ AI의 감정/철학/이야기 부재와 예술 판단이 연결됨")
    else:
        r.reasons.append("㉡에 '감정·철학·이야기 부재'와 '예술로 보기 어려움'이 함께 필요함")

    if ok_c:
        r.positives.append("㉢ 미술계 변화 또는 예술 범주 확장의 가치가 드러남")
    else:
        r.reasons.append("㉢에 '미술계 변화/예술 범주 확장'과 '가치/의미'가 함께 필요함")

    if wrong:
        r.reasons.append("인간 예술의 특성을 AI의 특성으로 잘못 옮긴 오개념이 있음")

    r.passed = ok_a and ok_b and ok_c and not wrong
    return r


# =========================================================
# 8. 영상 기획안 채점
# =========================================================

VIDEO_RULES = {
    1: {
        "visual_groups": [
            ["혼자", "한 명", "홀로"],
            ["조용", "차분", "집중", "어려운 문제", "도전적인 과제"],
        ],
        "audio_groups": [
            ["조용", "무음", "소리를 줄", "소음 최소", "연필", "책장", "잔잔"],
        ],
        "effect_source": [
            "혼자",
            "차분",
            "집중",
            "어려운 과제",
            "도전적인 과제",
            "익숙해질 때까지",
            "연습",
        ],
        "wrong_visual": ["친구들과 함께", "여럿이 함께", "떠들며 공부"],
        "wrong_audio": ["경쾌한 음악", "시끄러운", "큰 소리", "활기찬 음악"],
    },
    2: {
        "visual_groups": [
            ["고여", "고인", "멈춘", "정지", "흐르지"],
        ],
        "audio_groups": [
            ["무음", "조용", "흐르는 소리가 없", "물소리를 넣지", "정적", "소리를 줄"],
        ],
        "effect_source": [
            "전하",
            "이동하지",
            "머물",
            "정지",
            "고여 있는 물",
            "고인 물",
            "흐르지",
        ],
        "wrong_visual": ["폭포", "거세게 흐르는", "물레방아를 힘차게"],
        "wrong_audio": ["거센 물소리", "콸콸", "웅장한 물소리"],
    },
    3: {
        "visual_groups": [
            ["감정", "경험", "철학", "관점", "추억", "관객", "감동", "화가", "작가"],
        ],
        "audio_groups": [
            ["감정", "따뜻", "잔잔", "독백", "숨소리", "붓질", "사람 목소리", "내레이션"],
        ],
        "effect_source": [
            "감정",
            "철학",
            "삶의 경험",
            "관점",
            "환경",
            "감동",
            "울림",
            "인간의 예술",
            "작가",
        ],
        "wrong_visual": ["인공 지능이 감정을 느끼", "ai가 자신의 삶을 회상", "로봇이 감정을 담"],
        "wrong_audio": ["기계음만", "메트로놈만", "일정한 기계음"],
    },
}


def grade_video(set_no, visual, visual_effect, audio, audio_effect, scored=False):
    cfg = VIDEO_RULES[set_no]

    r = GradeResult(
        passed=True,
        score=0.0 if scored else None,
        max_score=6.0 if scored else None,
    )

    visual_ok = contains_all_groups(visual, cfg["visual_groups"])
    audio_ok = contains_all_groups(audio, cfg["audio_groups"])

    visual_wrong = contains_any(visual, cfg["wrong_visual"])
    audio_wrong = contains_any(audio, cfg["wrong_audio"])

    visual_source = contains_any(visual_effect, cfg["effect_source"])
    audio_source = contains_any(audio_effect, cfg["effect_source"])

    # 앞서 제시한 요소와 효과가 실제로 연결되는지 확인
    visual_link = (
        contains_any(
            visual_effect,
            [
                "화면",
                "모습",
                "장면",
                "보여",
                "시각",
                "혼자",
                "고여",
                "작가",
                "관객",
                "감정",
            ],
        )
        or (
            contains_any(visual, ["혼자", "고여", "작가", "관객", "화가"])
            and contains_any(visual_effect, cfg["effect_source"])
        )
    )

    audio_link = (
        contains_any(
            audio_effect,
            ["소리", "음악", "청각", "조용", "정적", "붓질", "기계음", "대비"],
        )
        or (
            contains_any(audio, ["무음", "조용", "음악", "소리", "붓질", "잔잔"])
            and contains_any(audio_effect, cfg["effect_source"])
        )
    )

    if set_no == 1:
        visual_conclusion = contains_any(
            visual_effect,
            ["혼자", "차분", "집중", "어려운 과제"],
        )
        audio_conclusion = contains_any(
            audio_effect,
            ["차분", "집중", "혼자", "조용"],
        )

    elif set_no == 2:
        visual_conclusion = contains_any(
            visual_effect,
            ["전하가 이동하지", "머물", "고여", "흐르지", "정전기"],
        )
        audio_conclusion = contains_any(
            audio_effect,
            ["전하가 이동하지", "머물", "흐르지", "정전기", "대비"],
        )

    else:
        visual_conclusion = contains_any(
            visual_effect,
            ["인간", "작가", "감정", "경험", "관점", "감동"],
        )
        audio_conclusion = contains_any(
            audio_effect,
            ["인간", "작가", "감정", "울림", "감동", "경험"],
        )

    if scored:
        # 총 6점
        # 시각 요소 1점
        # 시각 효과 2점
        # 청각 요소 1점
        # 청각 효과 2점
        if visual_ok and not visual_wrong:
            r.score += 1.0

        if visual_link and visual_source and visual_conclusion:
            r.score += 2.0

        if audio_ok and not audio_wrong:
            r.score += 1.0

        if audio_link and audio_source and audio_conclusion:
            r.score += 2.0

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
        visual_ok
        and audio_ok
        and not visual_wrong
        and not audio_wrong
        and visual_link
        and audio_link
        and visual_source
        and audio_source
        and visual_conclusion
        and audio_conclusion
    )

    return r


# =========================================================
# 9. Google Sheets 연결
# =========================================================

@st.cache_resource
def get_worksheet():
    """
    Streamlit Secrets의 서비스 계정 정보와 spreadsheet_id를 이용해
    Google Sheets 워크시트에 연결한다.
    """
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=scopes,
    )

    client = gspread.authorize(credentials)

    spreadsheet_id = st.secrets["google_sheet"]["spreadsheet_id"]
    worksheet_name = st.secrets["google_sheet"].get("worksheet_name", "학생답안")

    spreadsheet = client.open_by_key(spreadsheet_id)

    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=worksheet_name,
            rows=1000,
            cols=40,
        )

    return worksheet


def ensure_header(ws):
    header = [
        "제출시간",
        "학년",
        "반",
        "번호",
        "이름",
        "세트",
        "1번_답안1",
        "1번_답안2",
        "1번_답안3",
        "1번_판정",
        "2번_답안1",
        "2번_답안2",
        "2번_판정",
        "3번_시각요소",
        "3번_시각효과",
        "3번_청각요소",
        "3번_청각효과",
        "3번_판정",
        "3번_점수",
        "전체_재검토사유",
    ]

    first_row = ws.row_values(1)

    if first_row != header:
        if not first_row:
            ws.append_row(header)
        else:
            ws.insert_row(header, 1)


def append_submission(
    grade,
    class_no,
    student_no,
    name,
    set_no,
    q1_answers,
    q1_result,
    q2_answers,
    q2_result,
    q3_answers,
    q3_result,
):
    ws = get_worksheet()
    ensure_header(ws)

    submitted_at = datetime.now(
        ZoneInfo("Asia/Seoul")
    ).strftime("%Y-%m-%d %H:%M:%S")

    reasons = []
    reasons.extend(q1_result.reasons)
    reasons.extend(q2_result.reasons)
    reasons.extend(q3_result.reasons)

    q3_score = ""
    if q3_result.score is not None:
        q3_score = f"{q3_result.score:g}/{q3_result.max_score:g}"

    row = [
        submitted_at,
        grade,
        class_no,
        student_no,
        name,
        f"{set_no}세트",
        q1_answers[0],
        q1_answers[1],
        q1_answers[2],
        q1_result.status,
        q2_answers[0],
        q2_answers[1],
        q2_result.status,
        q3_answers[0],
        q3_answers[1],
        q3_answers[2],
        q3_answers[3],
        q3_result.status,
        q3_score,
        " | ".join(reasons),
    ]

    ws.append_row(row, value_input_option="USER_ENTERED")


# =========================================================
# 10. 세트별 채점 + 제출 함수
# =========================================================

def validate_student_info(grade, class_no, student_no, name):
    errors = []

    if not str(grade).strip():
        errors.append("학년을 입력하세요.")

    if not str(class_no).strip():
        errors.append("반을 입력하세요.")

    if not str(student_no).strip():
        errors.append("번호를 입력하세요.")

    if not name.strip():
        errors.append("이름을 입력하세요.")

    return errors


def render_submission_result(q1r, q2r, q3r):
    st.markdown("### 자동 채점 결과")
    c1, c2, c3 = st.columns(3)

    with c1:
        result_box("서·논술형1", q1r)

    with c2:
        result_box("서·논술형2", q2r)

    with c3:
        result_box("서·논술형3", q3r)


# =========================================================
# 11. UI
# =========================================================

st.title("📝 2회고사 대비 서·논술형 답안 작성 연습")
st.caption("학생 답안 제출 + 자동 1차 채점 + Google Sheets 저장")

with st.sidebar:
    st.header("학생 정보")

    grade = st.text_input("학년", placeholder="예: 2")
    class_no = st.text_input("반", placeholder="예: 3")
    student_no = st.text_input("번호", placeholder="예: 17")
    name = st.text_input("이름")

    set_no = st.selectbox(
        "연습 세트",
        [1, 2, 3],
        format_func=lambda x: f"{x}세트",
    )

    st.info(
        "답안을 모두 작성한 뒤 아래의 '답안 제출' 버튼을 누르세요."
    )


st.markdown("---")

# -------------------------
# 1번
# -------------------------

st.subheader("서·논술형1 — 표 빈칸 채우기")

q1a = st.text_input("㉠", key=f"s{set_no}_q1a")
q1b = st.text_input("㉡", key=f"s{set_no}_q1b")
q1c = st.text_input("㉢", key=f"s{set_no}_q1c")


# -------------------------
# 2번
# -------------------------

st.subheader("서·논술형2 — 설명 방법을 활용한 문장 쓰기")

st.caption(
    "각 문장 끝에 사용한 설명 방법을 괄호 안에 쓰세요. "
    "예: …라고 할 수 있다. (비교와 대조)"
)

q2a = st.text_area("(1)", key=f"s{set_no}_q2a", height=120)
q2b = st.text_area("(2)", key=f"s{set_no}_q2b", height=120)


# -------------------------
# 3번
# -------------------------

st.subheader("서·논술형3 — 영상 기획안")

q3v = st.text_area("Ⓐ 시각 요소", key=f"s{set_no}_q3v", height=110)
q3ve = st.text_area("Ⓐ 시각 요소의 효과", key=f"s{set_no}_q3ve", height=110)
q3a = st.text_area("Ⓑ 청각 요소", key=f"s{set_no}_q3a", height=110)
q3ae = st.text_area("Ⓑ 청각 요소의 효과", key=f"s{set_no}_q3ae", height=110)


st.markdown("---")


# -------------------------
# 제출
# -------------------------

if st.button("✅ 답안 제출", type="primary", use_container_width=True):
    info_errors = validate_student_info(
        grade,
        class_no,
        student_no,
        name,
    )

    answer_errors = []

    all_answers = [
        q1a,
        q1b,
        q1c,
        q2a,
        q2b,
        q3v,
        q3ve,
        q3a,
        q3ae,
    ]

    if any(not x.strip() for x in all_answers):
        answer_errors.append("모든 답안란을 작성한 뒤 제출하세요.")

    if info_errors or answer_errors:
        for e in info_errors + answer_errors:
            st.error(e)

    else:
        # 세트별 채점
        if set_no == 1:
            q1r = grade_1_1(q1a, q1b, q1c)

        elif set_no == 2:
            q1r = grade_2_1(q1a, q1b, q1c)

        else:
            q1r = grade_3_1(q1a, q1b, q1c)

        q2r = grade_explanation_pair(
            q2a,
            q2b,
            set_no,
        )

        q3r = grade_video(
            set_no,
            q3v,
            q3ve,
            q3a,
            q3ae,
            scored=(set_no == 3),
        )

        # 먼저 결과 표시
        render_submission_result(q1r, q2r, q3r)

        # Google Sheets 저장
        try:
            append_submission(
                grade=grade,
                class_no=class_no,
                student_no=student_no,
                name=name,
                set_no=set_no,
                q1_answers=[q1a, q1b, q1c],
                q1_result=q1r,
                q2_answers=[q2a, q2b],
                q2_result=q2r,
                q3_answers=[q3v, q3ve, q3a, q3ae],
                q3_result=q3r,
            )

            st.success(
                "답안이 제출되었고 Google 스프레드시트에 저장되었습니다."
            )

        except Exception as e:
            st.error(
                "자동 채점은 완료되었지만 Google 스프레드시트 저장에 실패했습니다."
            )
            with st.expander("오류 내용 보기"):
                st.code(str(e))


st.markdown("---")
st.caption(
    "※ 자동 채점은 1차 판정 보조용입니다. "
    "최종 성적 확정 전 교사가 답안을 확인하세요."
)
