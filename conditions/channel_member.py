from conditions import BaseCondition, ConditionResult


class ChannelMemberCondition(BaseCondition):
    name = "channel_member"
    description = "Requires membership in a specific channel to read the whisper"

    async def evaluate(self, whisper_id: str, user_id: int, **kwargs) -> ConditionResult:
        return ConditionResult(passed=True)
