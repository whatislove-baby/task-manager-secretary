"""The official graph's node budget must not preempt its model-call budget."""
from langchain_core.messages import AIMessage
from test_agent_runtime import AgentRuntimeTests, tool_call


class AgentGraphBudgetTests(AgentRuntimeTests):
    def test_thirty_five_tool_calls_complete_and_budget_resets_next_turn(self):
        conversation = None
        for turn in range(2):
            model = self.model(*[tool_call('list_tasks', {}, f'read-{turn}-{i}') for i in range(35)],
                               AIMessage(content='Finished the bounded inspection.'))
            args = {'conversationId': conversation} if conversation else {}
            run = self.start('Inspect the local task state within the supported tool budget.', **args)
            result = self.execute_run(run)
            self.assertEqual(result['status'], 'completed', result.get('error'))
            self.assertEqual(len(model.seen), 36)
            conversation = run['conversationId']

    def test_actual_fortieth_model_call_still_stops_further_calls(self):
        errors = []; original = self.service.agent._execute
        async def observe(id):
            try:
                return await original(id)
            except Exception as exc:
                errors.append(type(exc).__name__)
                raise
        self.service.agent._execute = observe
        model = self.model(*[tool_call('list_tasks', {}, f'read-{i}') for i in range(45)],
                           AIMessage(content='This should never be reached.'))
        result = self.execute_run(self.start('Exercise the bounded model-call guard.'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(errors, ['ModelCallLimitExceededError'])
        self.assertEqual(len(model.seen), 40)


# Reuse the real fixture methods without duplicating its 24 inherited tests.
for _name in list(vars(AgentRuntimeTests)):
    if _name.startswith('test_'):
        setattr(AgentGraphBudgetTests, _name, None)
del AgentRuntimeTests
