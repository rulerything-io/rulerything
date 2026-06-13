"""
Rulerything — 工具函数
"""

import re
from datetime import datetime


def _iso_now() -> str:
    """返回当前时间的 ISO 格式字符串。"""
    return datetime.now().isoformat()

from core.state import state


def enhance_prompt(user_input: str, max_rules: int = 5) -> str:
    """将用户问题与相关规则结合，生成增强提示词。

    用法::

        enhanced = enhance_prompt("如何在Python中高效处理百万条数据的循环？")
        # response = claude.chat(messages=[
        #     {"role": "system", "content": enhanced},
        #     {"role": "user", "content": user_input},
        # ])
    """
    if state.adaptive_system:
        results = state.adaptive_system.query(
            query_text=user_input,
            sort_by="title",
            use_semantic=True,
            limit=max_rules,
        )
    else:
        all_results = []
        for search_type in ("exact", "prefix", "tag"):
            results = state.index.search(user_input, search_type, limit=3)
            all_results.extend(results)

        keywords = re.findall(r'[a-zA-Z_+#.]+', user_input)
        for kw in keywords[:5]:
            kw_lower = kw.lower()
            tag_results = state.index.search_by_tag(kw_lower, limit=3)
            all_results.extend(tag_results)
            prefix_results = state.index.search_prefix(kw_lower, limit=3)
            all_results.extend(prefix_results)

        seen = set()
        unique = []
        for r in all_results:
            if r.id not in seen:
                seen.add(r.id)
                unique.append(r)
        unique.sort(key=lambda r: r.confidence, reverse=True)
        results = unique[:max_rules]

    if not results:
        return user_input

    rules_text = "\n\n".join(
        f"规则 {i + 1}: [{r.id}] {r.title}\n"
        f"分类: {r.category} | 置信度: {r.confidence:.2f}\n"
        f"内容: {r.content}"
        for i, r in enumerate(results[:max_rules])
    )

    return f"""你是一个具备专业知识的技术助手。以下是与当前问题相关的最佳实践规则，请优先参考这些规则来回答：

## 相关规则库
{rules_text}

## 用户问题
{user_input}

## 回答要求
- 优先基于以上规则给出建议
- 如果引用了某条规则，请明确标注规则 ID（如 [规则 python/001]）
- 如果规则不完全适用，解释理由并提供补充建议
- 如果规则不相关，忽略规则并正常回答
- 保持回答自然、有用"""
