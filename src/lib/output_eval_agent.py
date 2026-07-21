from __future__ import annotations
import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Optional

from strands import Agent
from .opencode_agent_tool import create_opencode_tool

logger = logging.getLogger(__name__)


def _parse_json_response(text: str) -> dict:
    """Extract and parse a JSON object from an LLM response.

    Handles markdown fences (```json ... ```) and falls back to a regex
    search for the last JSON object containing the expected keys.
    """
    text = text.strip()
    # Strip markdown fences
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Try the whole text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Last resort: find the last {...} block that looks like a score object
    for match in reversed(list(re.finditer(r"\{[\s\S]+?\}", text))):
        candidate = match.group(0)
        if '"scores"' in candidate or '"criteria"' in candidate:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"No valid JSON object found in LLM response: {text[:200]}")


@dataclass
class AIInteractionTrace:
    candidate_message: str
    ai_response: Optional[str] = None
    previous_interaction: Optional[AIInteractionTrace] = None

class CandidateMonitoring():
    def __init__(self, eval_agent: Agent, problem_statement: str, eval_instructions: Optional[str] = None,  eval_result_init: Any = None):
        self.eval_agent = eval_agent
        self.eval_inst = eval_instructions
        self.eval_res = eval_result_init
        self.problem_statement = problem_statement
        self.active_trace: Optional[AIInteractionTrace] = None

        

    def dispatch_to_evaluator(self, trace: AIInteractionTrace):
        ai_interaction = json.dumps(asdict(trace), indent=2)
        
            # <evaluation_instructions>
            #     {self.eval_inst}
            # </evaluation_instructions>        
        
        eval_prompt = f"""
            Analyze the candidate interaction with AI based on the given rubric.
            The candidate is trying to solve the problem given in the problem statement.
            If the 'previous_interaction_result' is not empty, you need to take it into consideration while generating the output for the current interaction.
            <problem_statement>
                {self.problem_statement}
            </problem_statement>
            <candidate_ai_interaction>
                {ai_interaction}
            </candidate_ai_interaction>
            <previous_interaction_result>
                {self.eval_res}
            </previous_interaction_result>
        """
        logger.debug("eval_prompt turn %s", ai_interaction[:120])
        response = self.eval_agent(eval_prompt)
        content = response.message["content"]
        eval_output = [c for c in content if "text" in c][0]["text"]
        self.eval_res = _parse_json_response(eval_output)
        return self.eval_res
        
class MultiEvaluator():

    def __init__(self, chat_log, proj_base_dir: str, problem_statement: str, output_system_prompt: str, interact_system_prompt: str, output_judge_model, turn_judge_model, final_judge_model) -> None:
        self.chat_log = chat_log
        self.proj_base_dir = proj_base_dir
        self.output_judge_model = output_judge_model
        self.turn_judge_model = turn_judge_model
        self.final_judge_model = final_judge_model
        self.problem_statement = problem_statement

        self.turn_eval_sys_prompt = interact_system_prompt
        self.sys_output_prompt_eval = output_system_prompt

        self.system_final_eval_prompt = """
            You are preparing a hiring evaluation summary for a human reviewer. You
            have two independent judge reports: one on code quality, one on the
            candidate's collaboration process with an AI assistant. Combine them into
            a single evidence-backed summary — do not just restate scores, connect
            them.

            Specifically call out:
            - Cases where process score and code score diverge (e.g. strong code but
            passive/unverified process, or messy code but strong debugging and
            self-correction) — these are the most decision-relevant cases for a
            human to look at closely.
            - Any red flags from either pass.
            - 2-3 direct quotes/evidence snippets a human could verify in under a
            minute.

            Do not produce a single blended numeric score as the headline output —
            present both dimensions separately so the human can weigh them according
            to the role's needs (e.g. a role needing strong AI-collaboration skill vs.
            one needing strong raw coding ability may weigh these differently).
        """
        self.turn_evaluator = CandidateMonitoring(
            eval_agent=Agent(name="eval-turn-agent", model=turn_judge_model, system_prompt=self.turn_eval_sys_prompt),
            problem_statement=problem_statement,
        )
        opencode_tool = create_opencode_tool(base_dir=self.proj_base_dir)
        self.eval_output_agent = Agent(
            name="eval-output-agent",
            model=self.output_judge_model,
            system_prompt=self.sys_output_prompt_eval,
            tools=[opencode_tool],
        )
        

    def eval_output(self):
        user_prompt = f"""
        The candidate was given the below problem statement to solve:
        <problem_statement>
            {self.problem_statement}
        </problem_statement>
        Evaluate the final submission by making use of the tool at your disposal.
        """
        result = self.eval_output_agent(user_prompt)
        content = result.message["content"]
        result_text = [c for c in content if "text" in c][0]["text"]
        self.output_eval_res = _parse_json_response(result_text)
        logger.debug("output_eval_res: %s", json.dumps(self.output_eval_res, indent=2))
    
    def eval_turns_human_candidate(self):
        prev_candidate_message = ""
        prev_ai_response = ""

        for i in range(0, len(self.chat_log), 2):
            candidate_message = self.chat_log[i]["payload"]["prompt"]
            ai_response = self.chat_log[i + 1]["payload"]["responseText"]

            current_interaction = AIInteractionTrace(
                candidate_message=candidate_message,
                ai_response=ai_response,
                previous_interaction=None if i == 0 else AIInteractionTrace(
                    candidate_message=prev_candidate_message,
                    ai_response=prev_ai_response,
                ),
            )
            turn_eval_res = self.turn_evaluator.dispatch_to_evaluator(current_interaction)
            prev_candidate_message = candidate_message
            prev_ai_response = ai_response

        logger.debug("turn_eval_res: %s", json.dumps(turn_eval_res, indent=2))
        self.turn_eval_res = turn_eval_res
        

    def eval_final(self):
        final_eval_prompt = f"""
        You are given output of two independent judges below; code quality judge and process judge.
        <code_quality_judge_output>
            {self.output_eval_res}
        </code_quality_judge_output>
        <process_judge_output>
            {self.turn_eval_res}
        </process_judge_output>
        Combine them into a single evidence-backed summary — do not just restate scores, connect them.

        Output format: short prose summary (150-250 words), followed by a
        decision-support table of the two weighted scores and top 3 pieces of
        evidence for each.
        """
        final_eval_agent = Agent(
            name="eval-final-agent",
            model=self.final_judge_model,
            system_prompt=self.system_final_eval_prompt,
        )
        response = final_eval_agent(final_eval_prompt)
        content = response.message["content"]
        response_text = [c for c in content if "text" in c][0]["text"]
        return (self.output_eval_res, self.turn_eval_res, response_text)