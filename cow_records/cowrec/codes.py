"""smaXtec 메모(ICAR 기반) 분류·질병·조치 코드 → 한글.

실측(2026-11): category 예 UDDER·METABOLISM, note_event 예 MASTITIS·KETOSIS·MILK_FEVER_ACUTE.
표에 없는 코드는 원문 그대로 표시 → 발견되면 여기에 추가.
"""
CATEGORY = {
    "UDDER": "유방", "METABOLISM": "대사", "REPRODUCTION": "번식", "FERTILITY": "번식", "CLAW": "발굽",
    "LIMBS": "다리·발굽", "LOCOMOTION": "보행", "DIGESTION": "소화기", "DIGESTIVE": "소화기",
    "RESPIRATORY": "호흡기", "INFECTIOUS": "전염성", "GENERAL": "일반", "CALF": "송아지", "OTHER": "기타",
    "SKIN": "피부", "PARASITES": "기생충", "INJURY": "외상",
}
EVENT = {
    "MASTITIS": "유방염", "MASTITIS_CLINICAL": "임상형 유방염", "MASTITIS_SUBCLINICAL": "준임상 유방염",
    "KETOSIS": "케토시스", "KETOSIS_SUBCLINICAL": "준임상 케토시스", "MILK_FEVER": "유열", "MILK_FEVER_ACUTE": "급성 유열",
    "HYPOCALCEMIA": "저칼슘혈증", "DISPLACED_ABOMASUM": "제4위 전위", "LDA": "좌측 제4위 전위", "RDA": "우측 제4위 전위",
    "ACIDOSIS": "산독증", "SARA": "아급성 반추위산독증", "BLOAT": "고창증", "INDIGESTION": "소화불량",
    "METRITIS": "자궁염", "ENDOMETRITIS": "자궁내막염", "RETAINED_PLACENTA": "후산정체", "CYSTIC_OVARY": "난소낭종",
    "ANOESTRUS": "무발정", "ABORTION": "유산", "DYSTOCIA": "난산",
    "LAMENESS": "파행", "DIGITAL_DERMATITIS": "지간피부염", "SOLE_ULCER": "제저궤양", "WHITE_LINE_DISEASE": "백선병",
    "PNEUMONIA": "폐렴", "RESPIRATORY_DISEASE": "호흡기 질병", "DIARRHEA": "설사", "FEVER": "발열",
    "INJURY": "외상", "OTHER": "기타",
}
ACTION = {
    "ANTIBIOTICS": "항생제", "ANTIBIOTIC": "항생제", "ANTI_INFLAMMATORY": "소염제", "NSAID": "소염진통제",
    "CALCIUM": "칼슘 투여", "GLUCOSE": "포도당", "PROPYLENE_GLYCOL": "프로필렌글리콜", "INFUSION": "수액",
    "HORMONE": "호르몬", "VET_VISIT": "수의사 진료", "SURGERY": "수술", "CLAW_TRIMMING": "삭제",
    "OBSERVATION": "관찰", "ISOLATION": "격리", "CULLING": "도태", "OTHER": "기타",
}
TODO_TYPE = {  # 알림 확인 기록(todo)의 data_type/sub_type
    "clinical_condition": "임상 의심 알림", "watchlist": "관찰 목록", "health": "건강 알림",
    "401_mastitis": "유방염 의심", "402_milk_fever": "유열 위험", "403_ketosis": "케토시스 의심",
}
CLOSING = {"user-ack": "확인함", "user-nack": "해당 없음 처리", "auto-ack": "자동 확인", "auto-nack": "자동 종료",
           "archived": "보관"}


def ko(table: dict, code) -> str:
    if not code:
        return ""
    c = str(code)
    return table.get(c.upper(), table.get(c, c.replace("_", " ").title()))
