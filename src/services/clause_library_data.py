"""AgentCore Platform v1.0 — bundled base compliance-clause library (data) for GOV-C2-026.

The committed, versioned bundled base clause library authored by the implementer (docs/02
"KB / Data Files"): the mandatory clauses per procurement regime, GROUNDED on authoritative public
sources. This is a STARTER base grounded on the public 総務省 standard-specification templates +
会計法 / 地方自治法施行令 relevant articles; the deploy operator MUST maintain the text and bump
``BUNDLED_VERSION`` on any law change (会計法 / APPI) — staleness maps directly to 国家賠償法 (State
Redress Act) liability (docs/02 "Update ownership"). ``BUNDLED_VERSION`` is bound into the S-4
audit_metadata (kb_version) on every invocation, so an output is traceable to the exact revision.
"""

from __future__ import annotations

from typing import Any


BUNDLED_VERSION = "2025.07-base"
SOURCE_PROVENANCE = "総務省 標準仕様書テンプレート（公開）/ 会計法・地方自治法施行令 関連条文 / METI 調達ガイドライン"

# clause id -> {source, text}. Ids match the code-baseline required set in clause_injection.
BUNDLED_CLAUSES: dict[str, Any] = {
    # --- common (every jurisdiction) ---
    "nyusatsu_koukoku": {
        "source": "会計法第29条の3 / 地方自治法第234条（一般競争入札）",
        "text": (
            "入札公告：本調達は一般競争入札により行う。入札参加資格、入札日時・場所、入札保証金、契約条件その"
            "他必要事項は入札公告に定めるところによる。公告期間は関係法令に定める期間以上を確保する。"
        ),
    },
    "appi_kojin_joho": {
        "source": "個人情報の保護に関する法律（APPI）/ 行政機関等の保有する個人情報の適正な取扱い",
        "text": (
            "個人情報保護：受注者は、本業務に関して知り得た個人情報を個人情報の保護に関する法律その他関係法令"
            "に従い適正に取り扱い、目的外利用・第三者提供・漏えいを行ってはならない。業務完了後は発注者の指示"
            "に従い返却又は消去し、その結果を報告する。"
        ),
    },
    "kensyu_joken": {
        "source": "会計法第29条の11（給付の完了の確認）/ 政府契約の支払遅延防止等に関する法律",
        "text": (
            "検収条件：受注者は成果物を納入し、発注者は仕様書に定める要件への適合を検査（検収）する。検収に合格"
            "した後でなければ対価の支払は行わない。不合格の場合、受注者は発注者の指定する期間内に是正する。"
        ),
    },
    # --- 会計法 (central) ---
    "maebaraikin_genya_central": {
        "source": "予算決算及び会計令第58条・第59条（前金払）",
        "text": (
            "前払金限度額（会計法・国）：前払金は契約金額の10分の4を超えない範囲で、予算決算及び会計令の定める"
            "ところにより支払うことができる。前払金の使用は当該契約の履行に必要な経費に限る。"
        ),
    },
    # --- 地方自治法 (municipal) ---
    "maebaraikin_genya_municipal": {
        "source": "地方自治法施行令第163条 / 各自治体 財務規則（前金払）",
        "text": (
            "前払金限度額（地方自治法・自治体）：前払金は当該自治体の財務規則に定める率（一般に契約金額の10分の"
            "4）を超えない範囲で支払うことができる。率・対象は当該自治体の規則によるため、適用時に確認を要する。"
        ),
    },
    "gikai_giketsu": {
        "source": "地方自治法第96条第1項第5号（議会の議決事件）",
        "text": (
            "議会議決 要否：予定価格が地方自治法及び当該自治体の条例に定める金額以上の工事又は製造の請負契約等に"
            "あっては、契約の締結について議会の議決を要する。該当の有無を締結前に確認する。"
        ),
    },
}
