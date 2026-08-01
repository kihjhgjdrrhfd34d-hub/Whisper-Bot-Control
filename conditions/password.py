import hashlib
import logging

from conditions import BaseCondition, ConditionResult
from database.whisper_conditions import get_condition_attempts, record_condition_attempt

logger = logging.getLogger(__name__)


class PasswordCondition(BaseCondition):
    name = "password"
    description = "Requires a password to read the whisper"
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
        condition_id = config.get("id", "password")
        hint = config.get("hint", "")
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
            message=f"🔐 هذه الهمسة محمية بكلمة سر.\nتلميح: {hint}" if hint
                    else "🔐 هذه الهمسة محمية بكلمة سر.",
            condition_id=condition_id,
        )

    def _handle_answer(
        self, whisper_id: str, user_id: int, config: dict, answer: str,
    ) -> ConditionResult:
        condition_id = config.get("id", "password")
        correct = _verify_password(answer.strip(), config)
        record_condition_attempt(
            whisper_id, user_id, self.name,
            passed=correct,
            attempt_data={"answer": answer.strip()},
        )
        if correct:
            return ConditionResult(
                passed=True, reason="correct_password",
                message="✅ كلمة السر صحيحة!",
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
            passed=False, reason="wrong_password",
            requires_interaction=True,
            message=f"❌ كلمة السر خطأ. لديك {remaining} محاولات متبقية.",
            condition_id=condition_id,
        )


def _filter_attempts(attempts: list, condition_name: str) -> list:
    return [a for a in attempts if a["condition_name"] == condition_name]


def _verify_password(answer: str, config: dict) -> bool:
    stored_hash = config.get("hash") or config.get("password", "")
    if not stored_hash:
        return False

    algorithm = config.get("algorithm", "")
    salt = config.get("salt", "")

    if algorithm == "pbkdf2_sha256" and salt:
        iterations = config.get("iterations", 100_000)
        try:
            dk = hashlib.pbkdf2_hmac(
                "sha256",
                answer.encode("utf-8"),
                salt.encode("utf-8"),
                iterations,
                dklen=32,
            )
            return dk.hex() == stored_hash
        except Exception as e:
            logger.warning("[PASSWORD] pbkdf2 verify failed: %s", e)
            return False

    # Backward compat: plain text (old test fixtures without salt/algorithm)
    return answer == stored_hash
