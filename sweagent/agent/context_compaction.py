"""Summarize selected long middle tool outputs, preserving actions and tool IDs."""
import copy
import re

SUMMARY_PREFIX = '[Tool output summary (extractive; incomplete)'
IMPORTANT_LINE = re.compile(r'error|exception|traceback|failed|failure|passed|warning|exit.?code|return.?code|result|summary', re.I)


def text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(block, dict) and block.get('type') == 'text'
                                         and isinstance(block.get('text'), str) for block in content):
        return '\n'.join(block['text'] for block in content)
    return ''


def summarize_tool_output(content):
    """Bounded verbatim excerpts, not a model-generated interpretation."""
    # Text-block arrays are used by real histories; preserve multimodal results.
    text = text_content(content)
    if len(text) < 1000 or text.startswith(SUMMARY_PREFIX):
        return content
    content = text
    lines = content.splitlines()
    important = list(dict.fromkeys(line.strip() for line in lines if IMPORTANT_LINE.search(line)))
    excerpts = '\n'.join(line[:100] for line in important[-3:])
    summary = f'{SUMMARY_PREFIX}; original: {len(content)} chars, {len(lines)} lines]\n'
    summary += 'Beginning excerpt:\n' + content[:160]
    if excerpts:
        summary += '\nError/result excerpts:\n' + excerpts
    summary += '\nEnding excerpt:\n' + content[-160:]
    return summary


def compact_tool_outputs(messages, count_tokens, *, keep_first=5, keep_last=5, target_tokens=110000):
    """Summarize only enough middle outputs to fit; never mutate full history."""
    rounds = []
    current = None
    for index, message in enumerate(messages):
        if message.get('role') == 'assistant' and message.get('tool_calls'):
            current = []
            rounds.append(current)
        elif message.get('role') == 'tool' and current is not None:
            current.append(index)
    compacted = copy.deepcopy(messages)
    candidates = [index for group in rounds[keep_first:max(keep_first, len(rounds)-keep_last)] for index in group]
    candidates.sort(key=lambda index: len(text_content(messages[index].get('content'))), reverse=True)
    tokens = count_tokens(compacted)
    summarized = 0
    for index in candidates:
        if tokens <= target_tokens:
            break
        original = compacted[index]
        summary = summarize_tool_output(original['content'])
        if summary == original['content']:
            continue
        compacted[index] = dict(original, content=summary)
        compacted[index].pop('cache_control', None)
        after = count_tokens(compacted)
        if after >= tokens:
            compacted[index] = original
            continue
        summarized += 1
        tokens = after
    return compacted, tokens, summarized
