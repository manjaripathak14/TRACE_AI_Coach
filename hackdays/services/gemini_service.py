import json
import re
from typing import Any, Dict

from google import genai


class GeminiService:
    def __init__(self, api_key: str | None, model: str):
        self.api_key = (api_key or "").strip()
        self.model = model or "gemini-2.5-flash"
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None

    def is_configured(self) -> bool:
        return bool(self.client)

    def _parse_json_response(self, text: str) -> Dict[str, Any]:
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("Gemini returned an empty response.")

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    raise ValueError("Gemini returned malformed JSON.") from None
            else:
                raise ValueError("Gemini returned malformed JSON.")

        if not isinstance(parsed, dict):
            raise ValueError("Gemini response must be a JSON object.")
        return parsed

    def _generate(self, prompt: str) -> Dict[str, Any]:
        if not self.client:
            raise RuntimeError("GEMINI_API_KEY is not configured. Add it to your environment before using Gemini features.")

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={
                "temperature": 0.35,
                "max_output_tokens": 1000,
                "response_mime_type": "application/json",
            },
        )

        text = getattr(response, "text", None)
        if not text:
            raise ValueError("Gemini returned an empty response.")
        return self._parse_json_response(text)

    def analyze_code(self, problem_statement: str, language: str, code: str, user_profile: Dict[str, Any] | None = None) -> Dict[str, Any]:
        profile = user_profile or {}
        prompt = f"""
        You are TRACE, an AI coding coach for students and developers.
        Evaluate the following coding submission.

        Requirements:
        - Identify whether the submitted solution is likely correct, incomplete, or buggy.
        - Explain the algorithm and reasoning clearly.
        - Identify likely bugs or missing edge cases.
        - Explain time and space complexity.
        - Suggest focused improvements.
        - If the user supplied not enough information, say so and avoid pretending certainty.
        - Keep the answer structured and useful for learning.

        User profile: {json.dumps(profile, ensure_ascii=True)}
        Problem: {problem_statement or 'No problem description provided.'}
        Language: {language or 'Unknown'}
        Code:
        {code or 'No code submitted.'}

        Return JSON only with fields:
        {{
          "confidence": "high|medium|low",
          "status": "correct|incomplete|buggy|insufficient_info",
          "summary": "short explanation",
          "approach": "step-by-step algorithm explanation",
          "likely_issues": ["issue 1", "issue 2"],
          "edge_cases": ["case 1", "case 2"],
          "time_complexity": "O(...)",
          "space_complexity": "O(...)",
          "improvements": ["improvement 1", "improvement 2"],
          "next_steps": ["action 1", "action 2"]
        }}
        """
        return self._generate(prompt)

    def build_study_roadmap(self, profile: Dict[str, Any], weak_topics: list[str] | None = None, history: list[Dict[str, Any]] | None = None) -> Dict[str, Any]:
        prompt = f"""
        You are TRACE, a career coach and coding mentor.

        Create a practical 4-week learning roadmap for the following learner.

        Profile: {json.dumps(profile, ensure_ascii=True)}
        Weak topics: {json.dumps(weak_topics or [])}
        Recent history: {json.dumps(history or [])}

        Return JSON only with fields:
        {{
          "title": "Roadmap title",
          "overview": "short overview",
          "weeks": [
            {{
              "week": 1,
              "focus": "focus area",
              "goals": ["goal 1", "goal 2"],
              "practice": ["practice 1", "practice 2"]
            }}
          ],
          "recommended_topics": ["topic 1", "topic 2"],
          "daily_plan": ["task 1", "task 2", "task 3"]
        }}
        """
        return self._generate(prompt)

    def generate_interview_question(self, company: str, role: str, level: str, topic: str | None = None) -> Dict[str, Any]:
        prompt = f"""
        Generate a realistic technical interview question for a candidate.

        Company: {company or 'Any'}
        Role: {role or 'Developer'}
        Level: {level or 'Intermediate'}
        Topic: {topic or 'General coding'}

        Return JSON only with fields:
        {{
          "question": "the interview question",
          "expected_approach": "how a strong candidate should approach it",
          "follow_up": "one good follow-up question",
          "difficulty": "easy|medium|hard"
        }}
        """
        return self._generate(prompt)

    def evaluate_interview_answer(self, question: str, answer: str, role: str | None = None, level: str | None = None) -> Dict[str, Any]:
        prompt = f"""
        You are TRACE, an interview coach.
        Evaluate the candidate's answer.

        Role: {role or 'Developer'}
        Level: {level or 'Intermediate'}
        Question: {question or 'No question provided'}
        Candidate answer: {answer or 'No answer provided'}

        Return JSON only with fields:
        {{
          "score": 0,
          "strengths": ["strength 1", "strength 2"],
          "gaps": ["gap 1", "gap 2"],
          "feedback": "constructive feedback",
          "improvement_plan": ["step 1", "step 2"],
          "next_question": "one good follow-up question"
        }}
        """
        return self._generate(prompt)

    def generate_mock_summary(self, questions: list[str], answers: list[str], profile: Dict[str, Any] | None = None) -> Dict[str, Any]:
        prompt = f"""
        Summarize a mock interview session and provide actionable feedback.

        Profile: {json.dumps(profile or {}, ensure_ascii=True)}
        Questions: {json.dumps(questions, ensure_ascii=True)}
        Answers: {json.dumps(answers, ensure_ascii=True)}

        Return JSON only with fields:
        {{
          "overall_score": 0,
          "strengths": ["strength 1", "strength 2"],
          "areas_to_improve": ["area 1", "area 2"],
          "final_feedback": "summary of performance",
          "practice_next": ["next practice task 1", "next practice task 2"]
        }}
        """
        return self._generate(prompt)

    def generate_performance_insights(self, profile: Dict[str, Any], history: list[Dict[str, Any]]) -> Dict[str, Any]:
        prompt = f"""
        You are TRACE, an evidence-based coding coach. Analyze only the supplied saved activity.
        Do not invent problem counts, success rates, weak topics, or trends. If the history is
        empty or insufficient, state that clearly and recommend what to record next.

        Learner profile: {json.dumps(profile, ensure_ascii=True)}
        Saved activity: {json.dumps(history, ensure_ascii=True)}

        Return JSON only with fields:
        {{
          "summary": "evidence-based summary",
          "strengths": ["observed strength or say insufficient data"],
          "focus_topics": ["topic grounded in saved activity"],
          "recommendations": ["specific next action"],
          "data_limitations": ["what cannot be concluded from this activity"]
        }}
        """
        return self._generate(prompt)
