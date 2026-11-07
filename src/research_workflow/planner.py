from __future__ import annotations

import re

from .models import QueryPlan


ACADEMIC_MARKERS = {
    "paper",
    "论文",
    "dataset",
    "benchmark",
    "评测",
    "方法",
    "arxiv",
    "acl",
    "citation",
}

TOOL_SELECTION_MARKERS = {
    "api",
    "webhook",
    "webhooks",
    "custom field",
    "custom fields",
    "自定义字段",
    "依赖关系",
    "状态",
    "优先级",
    "kanban",
    "看板",
    "table",
    "表格",
    "排程",
    "agent",
    "工具",
    "tool",
}

INVENTORY_MARKERS = {
    "深度搜索",
    "深度研究",
    "事实核查",
    "fact checking",
    "fact-checking",
    "deep research",
    "deep search",
    "research agent",
    "search agent",
    "skill",
    "skills",
    "开源项目",
    "open source project",
    "github",
}

DOMAIN_SAFETY_MARKERS = {
    "老人",
    "老年",
    "照护",
    "护理",
    "跌倒",
    "走失",
    "诈骗",
    "用药",
    "谵妄",
    "幻觉",
    "认知障碍",
    "痴呆",
    "疾病",
    "医疗",
    "健康",
    "预防",
    "火灾",
    "烫伤",
    "家属",
    "caregiver",
    "older adult",
    "elderly",
    "dementia",
    "delirium",
    "fall prevention",
}


def plan_query(topic: str, mode: str = "daily") -> QueryPlan:
    cleaned = " ".join(topic.split())
    if mode == "auto" and _looks_tool_selection(cleaned):
        effective_mode = "tool_selection"
    elif mode == "auto" and _looks_academic(cleaned):
        effective_mode = "academic"
    else:
        effective_mode = mode
    if effective_mode == "auto":
        effective_mode = "daily"

    if effective_mode == "tool_selection":
        subquestions = [
            f"{cleaned} 的必需功能和非目标是什么？",
            f"{cleaned} 有哪些候选工具或服务？",
            f"{cleaned} 的 API、Webhook、轮询和权限模型是否适合 AI agent 读写？",
            f"{cleaned} 是否支持优先级、依赖关系、状态、自定义字段和排程参数？",
            f"{cleaned} 的人类看板/表格修正体验和自托管成本如何？",
        ]
    elif _looks_domain_safety(cleaned):
        subquestions = [
            f"{cleaned} 涉及哪些常见风险场景？",
            f"{cleaned} 哪些表现提示需要医疗、法律或专业帮助？",
            f"{cleaned} 有哪些官方、医学或机构指南支持的预防措施？",
            f"{cleaned} 家属或照护者可以设置哪些低成本防线？",
            f"{cleaned} 哪些说法容易被误解、夸大或污名化？",
        ]
    else:
        subquestions = [
            f"{cleaned} 的核心定义和边界是什么？",
            f"{cleaned} 有哪些主要观点、方案或实践路径？",
            f"{cleaned} 的最新状态、时间线或适用条件是什么？",
            f"{cleaned} 的关键证据来源有哪些？",
            f"{cleaned} 存在哪些争议、限制或常见误判？",
        ]
    if effective_mode == "academic":
        subquestions.extend(
            [
                f"{cleaned} 相关论文、数据集和 benchmark 是什么？",
                f"{cleaned} 的方法假设、评价指标和可复现性如何？",
            ]
        )

    zh_keywords = _keyword_variants(cleaned, language="zh", mode=effective_mode)
    en_keywords = _keyword_variants(cleaned, language="en", mode=effective_mode)
    if effective_mode == "tool_selection":
        source_targets = [
            "official product/API documentation",
            "webhook and REST/GraphQL API references",
            "GitHub repository, release, and issue activity for open-source tools",
            "pricing/self-hosting/deployment documentation",
            "human UI references for board/table views",
        ]
    elif _looks_domain_safety(cleaned):
        source_targets = [
            "official public-health, safety, or government guidance",
            "medical references or clinical guidance when symptoms are involved",
            "institutional care/caregiver guidance",
            "systematic reviews or evidence summaries when interventions are debated",
            "community reports only as lived-signal context, not primary evidence",
        ]
    else:
        source_targets = [
            "SearXNG aggregated web results",
            "official or institutional references",
            "high-quality secondary explainers",
        ]
    if effective_mode == "academic":
        source_targets.extend(["Google Scholar or Semantic Scholar", "arXiv/ACL Anthology/PubMed when relevant"])
    elif effective_mode != "tool_selection":
        source_targets.extend(["Brave/Kagi/Google fallback", "community discussions for lived usage signals"])

    return QueryPlan(
        topic=cleaned,
        mode=effective_mode,
        core_question=f"围绕“{cleaned}”建立可追溯、可核查的研究结论。",
        subquestions=subquestions,
        zh_keywords=zh_keywords,
        en_keywords=en_keywords,
        source_targets=source_targets,
        high_risk_watchlist=[
            "precise dates, version numbers, and project maintenance status",
            "performance, cost, adoption, or benchmark numbers",
            "claims about censorship, legality, medical, financial, or safety impact",
            "citations that may not actually support the sentence",
        ],
    )


def _looks_academic(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ACADEMIC_MARKERS)


def _looks_tool_selection(text: str) -> bool:
    lowered = text.lower()
    marker_hits = sum(1 for marker in TOOL_SELECTION_MARKERS if marker in lowered)
    return marker_hits >= 2 or ("inbox" in lowered and ("agent" in lowered or "api" in lowered))


def _keyword_variants(topic: str, language: str, mode: str = "daily") -> list[str]:
    words = [w for w in re.split(r"[\s,，;；/]+", topic) if w]
    variants = [topic]
    if mode == "tool_selection":
        if language == "en":
            variants.extend(
                [
                    f"{topic} API documentation",
                    f"{topic} webhook custom fields",
                    f"{topic} dependencies kanban table",
                    f"{topic} open source self hosted",
                ]
            )
        else:
            variants.extend(
                [
                    f"{topic} API 文档",
                    f"{topic} webhook 自定义字段",
                    f"{topic} 依赖关系 看板 表格",
                    f"{topic} 开源 自托管",
                ]
            )
    elif _looks_domain_safety(topic):
        if language == "en":
            variants.extend(
                [
                    f"{topic} official guidance",
                    f"{topic} prevention checklist",
                    f"{topic} caregiver safety",
                ]
            )
        else:
            variants.extend(
                [
                    f"{topic} 官方 指南",
                    f"{topic} 预防 清单",
                    f"{topic} 家属 照护 安全",
                ]
            )
    elif _looks_inventory_topic(topic) or language == "en" and _looks_academic(topic):
        variants.extend(
            [
                f"{topic} open source",
                f"{topic} benchmark",
                f"{topic} GitHub",
            ]
        )
    elif language == "en":
        variants.extend(
            [
                f"{topic} official sources",
                f"{topic} best practices",
                f"{topic} evidence",
            ]
        )
    else:
        variants.extend(
            [
                f"{topic} 官方",
                f"{topic} 最佳实践",
                f"{topic} 对比",
            ]
        )
    variants.extend(words[:4])
    return list(dict.fromkeys(variants))


def _looks_inventory_topic(text: str) -> bool:
    lowered = text.lower()
    if _looks_domain_safety(text) and not any(marker in lowered for marker in ["github", "开源项目", "open source"]):
        return False
    marker_hits = sum(1 for marker in INVENTORY_MARKERS if marker in lowered)
    return marker_hits >= 1 and any(
        marker in lowered
        for marker in ["有哪些", "清单", "列表", "对比", "best", "推荐", "project", "项目", "工具", "tool", "method", "方法", "framework", "系统"]
    )


def _looks_domain_safety(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in DOMAIN_SAFETY_MARKERS)


def uses_project_discovery(topic: str, mode: str = "daily") -> bool:
    return mode == "tool_selection" or _looks_inventory_topic(topic) or (mode == "academic" and _looks_academic(topic))
