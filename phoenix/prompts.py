from __future__ import annotations

import json
import logging


class PromptBuilder:
    def __init__(self):
        self.logger = logging.getLogger("phoenix.prompts")

    def _build_comparison_prompt(self, problem_statement: str, samples: list[dict], num_samples: int, select_top_half: bool = False) -> str:
        if select_top_half:
            import math
            top_n = math.ceil(num_samples / 2)
            prompt = f"""You are an expert code debugging analyst. Compare multiple solution attempts for the same SWE-bench task and select the TOP {top_n} best patches (approximately half).

## Problem Statement
{problem_statement}

## Solution Attempts
**IMPORTANT:** The samples are presented in RANDOM ORDER to avoid position bias. Evaluate each solution independently based on technical merit only.
"""
            for sample in samples:
                prompt += f"""### Sample {sample['index']}
**Execution Summary:** {len(sample['trajectory'])} steps, Exit: {sample['exit_status']}, Patch: {'Yes' if sample['submission'] else 'No'}
"""
                if sample.get('regression_evidence'):
                    prompt += '\n**Measured repository regression evidence:**\n' + json.dumps(sample['regression_evidence'], ensure_ascii=False) + '\n'
                if sample["submission"]:
                    prompt += f"\n**Patch:**\n```\n{sample['submission']}\n```\n\n"
                else:
                    prompt += "\n**Patch:** No submission\n\n"

            prompt += f"""## Analysis Requirements

Compare all samples and provide:

1. **Select Top {top_n} Samples:** Which sample numbers have the most correct solutions?

2. **Reasoning:** Why is this sample better than others? Consider:
   - Patch correctness and completeness
   - Problem understanding accuracy
   - Edge case handling
   
3. **Key Insights for Next Attempt:** What can we learn from all attempts to guide the next solution? Be specific about:
   - Which approaches worked well
   - Specific files/functions that need attention

## Output Format
**You MUST strictly follow this format:**

**TOP_SAMPLES:** [comma-separated list, e.g., "1,3,5,7"]

**REASONING:**
[Explanation of why these {top_n} samples are best]
"""
        else:
            prompt = f"""You are an expert code debugging analyst. Compare multiple solution attempts for the same SWE-bench task and identify the best generation patch.

## Problem Statement
{problem_statement}

## Solution Attempts
**IMPORTANT:** The samples are presented in RANDOM ORDER to avoid position bias. Evaluate each solution independently based on technical merit only.
"""
            for sample in samples:
                prompt += f"""### Sample {sample['index']}
**Execution Summary:** {len(sample['trajectory'])} steps, Exit: {sample['exit_status']}, Patch: {'Yes' if sample['submission'] else 'No'}
"""
                if sample.get('regression_evidence'):
                    prompt += '\n**Measured repository regression evidence:**\n' + json.dumps(sample['regression_evidence'], ensure_ascii=False) + '\n'
                if sample['submission']:
                    prompt += f"\n**Patch:**\n```\n{sample['submission']}\n```\n\n"
                else:
                    prompt += "\n**Patch:** No submission\n\n"
            prompt += f"""## Analysis Requirements

Compare all samples and provide:

1. **Best Sample Selection:** Which sample number (1-{num_samples}) has the most correct solution?

2. **Reasoning:** Why is this sample better than others? Consider:
   - Patch correctness and completeness
   - Problem understanding accuracy
   - Edge case handling
   
3. **Key Insights for Next Attempt:** What can we learn from all attempts to guide the next solution? Be specific about:
   - Which approaches worked well
   - Specific files/functions that need attention

## Output Format

**BEST_SAMPLE:** [number]

**REASONING:**
[Detailed comparison explaining why this sample is best]

**INSIGHTS FOR NEXT ATTEMPT:**
[Specific, actionable guidance for the {num_samples + 1}th attempt based on learnings from all {num_samples} samples]

Keep response under 600 words. Focus on concrete technical differences and actionable insights.
"""
        return prompt

    def _generate_location_insights_from_patch(self, patch_data: dict, previous_round_info: dict = None) -> str:
        """从 patch data 生成 location insights，包含前一轮的反思"""
        insights_parts = []
        compiled = patch_data.get('schema_version') in ('location_candidate_v3', 'location_candidate_v4')
        if patch_data.get('hypothesis'):
            insights_parts.append('**Candidate repair strategy (unverified):**')
            insights_parts.append(f"Candidate: {patch_data.get('id', 'unspecified')}")
            insights_parts.append(patch_data['hypothesis'])
            if patch_data.get('repair_plan'):
                insights_parts.append(patch_data['repair_plan'])
            if patch_data.get('evidence'):
                insights_parts.append('**Source evidence:**')
                insights_parts.extend('- ' + item for item in patch_data['evidence'])
            if patch_data.get('unresolved_questions'):
                insights_parts.append('**Questions to resolve while implementing this candidate:**')
            for item in patch_data.get('unresolved_questions', []):
                if isinstance(item, dict):
                    insights_parts.append('- ' + item['question'] if compiled else
                                          f"- [{', '.join(item['location_ids'])}] {item['question']}")
                else:
                    insights_parts.append('- ' + item)  # legacy localization artifacts
            if patch_data.get('selected_location_choices'):
                insights_parts.append('**Location choices already selected for this candidate:**')
                for choice in patch_data['selected_location_choices']:
                    refs = ', '.join(choice['location_ids']) or 'no additional edit'
                    insights_parts.append(f"- {choice['question']} => {choice['choice']} [{refs}]: {choice['explanation']}")
            if patch_data.get('requirement_coverage'):
                insights_parts.append('**Coverage of the issue requirements:**')
                for item in patch_data['requirement_coverage']:
                    if patch_data.get('planning_mode') == 'complete_strategy_v1':
                        requirement = next(r['text'] for r in patch_data['requirements'] if r['id'] == item['requirement_id'])
                    else:
                        requirement = patch_data['requirements'][item['requirement_index']-1]
                    refs = ', '.join(item['location_ids']) or 'already satisfied; verify evidence'
                    insights_parts.append(f"- {requirement} [{refs}]: {item['explanation']}")
                    if item.get('evidence'):
                        insights_parts.append('  Current-source evidence: ' + json.dumps(item['evidence'], ensure_ascii=False))
            if patch_data.get('planning_mode') == 'complete_strategy_v1':
                insights_parts.append('**Whole repair mechanism:**\n' + patch_data['implementation_outline'])
                insights_parts.append('**Cooperating changes and unchanged dependencies:**')
                for item in patch_data['dependency_review']:
                    insights_parts.append(f"- {item['area']} ({item['status']}): {item['explanation']} "
                                          f"[{', '.join(item['location_ids'])}]")
                    if item.get('evidence'):
                        insights_parts.append('  Evidence: ' + json.dumps(item['evidence'], ensure_ascii=False))
            insights_parts.append(
                'This is one complete alternative repair plan, not a proven fix. Evaluate its hypothesis '
                'and unresolved questions against the issue and current source while implementing the patch. '
                'If it cannot satisfy the requirements, report the concrete contradiction; do not silently '
                'switch to another location combination or claim validation without evidence.')
        if patch_data.get('schema_version') == 'location_checks_v1':
            insights_parts.append(f"Candidate: {patch_data['id']} (one complete location set)")
            if patch_data.get('planning_mode') in ('locations_only_v1', 'complete_strategy_v1'):
                insights_parts.append('Localization supplied locations and short intentions only, not an implementation or tests. '
                                      'Implement this complete plan and derive the necessary tests and verification from the issue '
                                      'and current source during patch generation. If it cannot address the issue, report why.')
            else:
                insights_parts.append('**Verification points for this location set:**')
                for q in patch_data['unresolved_questions']:
                    insights_parts.append(f"- [{', '.join(q['location_ids'])}] {q['question']}")
                insights_parts.append('Implement the cooperating changes at the complete locations below and verify these points. '
                                      'If the set cannot address the issue, report the concrete problem.')
        if previous_round_info:
            prev_round = previous_round_info.get('round', -1)
            prev_patch = previous_round_info.get('patch', '')
            prev_status = previous_round_info.get('exit_status', 'unknown')
            insights_parts.append("**Previous Attempt Reflection:**\n")
            insights_parts.append(f"In Round {prev_round}, we attempted to fix this location with the following approach:\n")
            insights_parts.append(f"- Exit Status: {prev_status}")
            if prev_patch:
                insights_parts.append(f"\n**Previous Patch:**")
                insights_parts.append("```python")
                insights_parts.append(prev_patch)
                insights_parts.append("```\n")
                insights_parts.append("**Instructions for This Round:**")
                insights_parts.append("- Reflect on the previous patch")
                insights_parts.append("- Improve upon the previous solution")
        files_to_modify = patch_data.get('files_to_modify', [])
        insights_parts.append("**Error Code Location Information:**\n")
        insights_parts.append("I will provide you with the location information of the buggy code below. ")
        insights_parts.append("Please make your modifications at the locations specify for the erroneous code.\n\n")
        for file_info in files_to_modify:
            file_path = file_info.get('file_path', 'unknown')
            modifications = file_info.get('modifications', [])
            insights_parts.append(f"### File: `{file_path}`\n")
            creating = file_info.get('operation') == 'create'
            if creating:
                insights_parts.append('**New file to create during patch generation. No original source lines exist.**')
            for idx, mod in enumerate(modifications, 1):
                location = mod.get('location', {})
                current_code = mod.get('current_code', '').strip()
                insights_parts.append(f"**Modification {idx}:**")
                if mod.get('id'):
                    insights_parts.append(f"- **Location ID:** {mod['id']}")
                if mod.get('reason') and not compiled:  # Already stated once in the generated plan.
                    insights_parts.append(f"- **Intended change:** {mod['reason']}")
                elif compiled:
                    insights_parts.append(f"- **Plan changes:** {', '.join(mod['change_ids'])}")
                if creating:
                    continue
                insights_parts.append(f"- **Source lines:** {location.get('start_line')}-{location.get('end_line')}")
                insights_parts.append(f"- **Current Code:**")
                insights_parts.append(f"```python")
                insights_parts.append(current_code)
                insights_parts.append("```\n")
        insights_parts.append("\n**Final Instructions:**")
        if previous_round_info:
            insights_parts.append("- Learn from the previous attempt and improve your solution")
        if patch_data.get('planning_mode') == 'complete_strategy_v1':
            insights_parts.append('- Implement this strategy as a whole and check every requirement, including unchanged behavior. '
                                  'The plan is a hypothesis, not an oracle. If current source reveals a necessary cooperating '
                                  'edit outside its anchors, inspect and include it; explain that addition and its requirement. '
                                  'Do not silently switch mechanisms or omit a requirement to fit the listed ranges. '
                                  'Run relevant current-repository checks, record results honestly, and inspect the final diff '
                                  'against the requirement coverage before submission.')
        else:
            insights_parts.append("- Please make modifications at the code locations mentioned above")
            insights_parts.append("- Do NOT make changes outside these locations")
        return "\n".join(insights_parts)

    def _enhance_agent_config_with_insights(self, original_config: AgentConfig, insights: str, num_samples: int) -> AgentConfig:
        """使用insights增强agent配置"""
        enhanced_config = original_config.model_copy(deep=True)
        # enhanced_config.use_change_history_method = False
        original_instance_template = enhanced_config.templates.instance_template
        if insights.strip():
            enhanced_instance_template = f"""Based on {num_samples} previous solution attempts at different code locations for this task, we have conducted a comparative analysis to identify the best approach and key learnings:

{insights}

Please solve the task by leveraging these insights from previous attempts. Focus on:
1. Building upon the strengths identified in the best approach
2. Avoiding the common mistakes observed across attempts
3. Addressing the specific areas that need attention

# Original Task Instructions
{original_instance_template}
"""
        else:
            enhanced_instance_template = original_instance_template
        enhanced_config.templates.instance_template = enhanced_instance_template
        self.logger.info(f"Enhanced instance template with comparison insights (length: {len(insights)} chars)")
        return enhanced_config

    def _enhance_agent_config_with_location_insights(self, original_config: AgentConfig, location_insights: str) -> AgentConfig:
        """使用location insights增强agent配置"""
        enhanced_config = original_config.model_copy(deep=True)
        # Render the plan as literal text: source snippets may themselves contain Jinja syntax.
        literal_plan = '{{ ' + json.dumps(location_insights, ensure_ascii=False) + ' }}'
        enhanced_config.templates.system_template = (
            original_config.templates.system_template +
            "\n\n# Candidate repair plan (unverified)\n"
            "Use this plan as guidance while following the original issue requirements and the "
            "restriction to non-test source changes. Candidate locations do not authorize editing tests.\n" + literal_plan)
        self.logger.info("Added complete candidate plan to system template")
        return enhanced_config
