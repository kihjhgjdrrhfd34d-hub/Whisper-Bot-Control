import logging

from conditions import BaseCondition, ConditionResult
from database.whisper_conditions import get_condition_attempts, record_condition_attempt
from conditions.password import _filter_attempts, _verify_password

logger = logging.getLogger(__name__)


class QuestionCondition(BaseCondition):
    name = "question"
    description = "Requires answering a question to read the whisper"
    MAX_ATTEMPTS = 3

    async def evaluate(self, whisper_id: str, user_id: int, **kwargs) -> ConditionResult:
        return self._check(whisper_id, user_id, kwargs)

    def check(self, whisper: dict, user_id: int, config: dict) -> ConditionResult:
        whisper_id = whisper.get("whisper_id", "")
        return self._check(whisper_id, user_id, config)

    def handle_interaction(
        self, call, bot, whisper: dict, user_id: int,
        config: dict, answer: str,
    ) -> ConditionResult:
        whisper_id = whisper.get("whisper_id", "")
        return self._handle_answer(whisper_id, user_id, config, answer)

    # ── internal ──────────────────────────────────────────────────────────

    def _check(self, whisper_id: str, user_id: int, config: dict) -> ConditionResult:
        condition_id = config.get("id", "question")
        question = config.get("question", "")
        attempts = _filter_attempts(
            get_condition_attempts(whisper_id, user_id), self.name,
        )
        if any(a["passed"] for a in attempts):
            return ConditionResult(
                passed=True, reason="already_satisfied",
                message="",
            )
        failed_count = sum(1 for a in attempts if not a["passed"])
        remaining = self.MAX_ATTEMPTS - failed_count
        if remaining <= 0:
            return ConditionResult(
                passed=False, reason="max_attempts_exceeded",
                requires_interaction=False,
                message="❌ لقد استنفذت جميع المحاولات.",
                condition_id=condition_id,
            )
        return ConditionResult(
            passed=False, reason="requires_input",
            requires_interaction=True,
            message=f"❓ أجب عن السؤال التالي:\n\n{question}" if question
                    else "❓ أجب عن السؤال التالي.",
            condition_id=condition_id,
        )

    def _handle_answer(
        self, whisper_id: str, user_id: int, config: dict, answer: str,
    ) -> ConditionResult:
        condition_id = config.get("id", "question")
        correct = _verify_password(answer.strip(), config)
        record_condition_attempt(
            whisper_id, user_id, self.name,
            passed=correct,
            attempt_data={
                "condition_type": "question",
                "result": "success" if correct else "failed",
            },
        )
        if correct:
            return ConditionResult(
                passed=True, reason="correct_answer",
                message="✅ إجابة صحيحة!",
                condition_id=condition_id,
            )
        attempts = _filter_attempts(
            get_condition_attempts(whisper_id, user_id), self.name,
        )
        failed_count = sum(1 for a in attempts if not a["passed"])
        remaining = self.MAX_ATTEMPTS - failed_count
        if remaining <= 0:
            return ConditionResult(
                passed=False, reason="max_attempts_exceeded",
                requires_interaction=False,
                message="❌ لقد استنفذت جميع المحاولات.",
                condition_id=condition_id,
            )
        return ConditionResult(
            passed=False, reason="wrong_answer",
            requires_interaction=True,
            message="❌ إجابة غير صحيحة، حاول مرة أخرى.",
            condition_id=condition_id,
        )
