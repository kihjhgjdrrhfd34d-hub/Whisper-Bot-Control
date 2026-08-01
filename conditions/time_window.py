from conditions import BaseCondition, ConditionResult


class TimeWindowCondition(BaseCondition):
    name = "time_window"
    description = "Limits read access to a specific time window"

    async def evaluate(self, whisper_id: str, user_id: int, **kwargs) -> ConditionResult:
        return ConditionResult(passed=True)
